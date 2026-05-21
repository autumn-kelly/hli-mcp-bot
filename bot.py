"""
JURA/CASO MCP 멀티에이전트 텔레그램 봇
=======================================
구조:
  사용자 → 텔레그램 봇
           → 오케스트레이터 (라우팅 + 조율)
             → 전략 에이전트
             → 영업기획 에이전트  
             → CRM 에이전트
           → 에이전트 간 자동 컨텍스트 전달
           → 최종 응답 텔레그램으로 발송

설치:
  pip install python-telegram-bot anthropic

실행:
  python bot.py
"""

import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import anthropic

# ── 환경변수 설정 ──────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "여기에_텔레그램_봇_토큰")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "여기에_ANTHROPIC_API_KEY")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))  # 본부장님 텔레그램 ID

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 에이전트 시스템 프롬프트 ───────────────────────────
AGENTS = {
    "strategy": {
        "name": "⚡ 전략 에이전트",
        "emoji": "⚡",
        "prompt": """당신은 JURA(스위스 프리미엄 전자동 커피머신)와 CASO DESIGN(혁신 주방가전) 전문 전략 에이전트입니다.
역할: 시장 인텔리전스, 브랜드 포지셔닝, CEO 보고용 전략 인사이트, 팀간 전략 방향 정의
JURA: 고관여·고가격·감성소구 / CASO: 혁신·디자인·라이프스타일 / 공통: 프리미엄 포지셔닝 유지
응답은 간결하고 실행 가능한 인사이트 중심으로 작성. 마지막에 영업기획팀·CRM팀·마케팅팀 액션 아이템 각 1-2개씩 제시."""
    },
    "sales": {
        "name": "📋 영업기획 에이전트",
        "emoji": "📋",
        "prompt": """당신은 JURA(100만~500만원대)와 CASO DESIGN(20만~80만원대) 전문 영업기획 에이전트입니다.
역할: 채널별 판매목표 수립, 판촉 기획, 영업 프로세스 표준화, 팀간 연계
원칙: 프리미엄 포지셔닝 유지, 가치 기반 판매
응답은 구체적 실행 계획과 KPI 중심으로 작성. 마지막에 마케팅팀 요청 사항과 CRM팀 필요 데이터 제시."""
    },
    "crm": {
        "name": "👥 CRM 에이전트",
        "emoji": "👥",
        "prompt": """당신은 JURA(소모품/AS/업그레이드 사이클)와 CASO DESIGN(크로스셀/라이프스타일) 전문 CRM 에이전트입니다.
역할: 고객 세그먼테이션, LTV 분석, 리텐션 캠페인 설계, 팀간 고객 데이터 공유
응답은 고객 세그먼트별 구체적 액션과 캠페인 시나리오 중심으로 작성. 마지막에 마케팅팀 캠페인 요청 제시."""
    }
}

ORCHESTRATOR_PROMPT = """당신은 JURA/CASO DESIGN 세일즈&마케팅 본부의 AI 오케스트레이터입니다.
사용자의 질문을 분석하여 어떤 에이전트가 처리해야 할지 판단하고, 필요시 여러 에이전트를 순차적으로 활성화합니다.

에이전트 역할:
- strategy: 시장분석, 경쟁사, 브랜드 전략, CEO 보고, 채널 전략
- sales: 판촉 기획, 영업 목표, 프로모션, B2B 영업, 채널 관리
- crm: 고객 세그먼트, LTV, 리텐션, 캠페인, 소모품/업그레이드 사이클

다음 JSON 형식으로만 응답하세요:
{
  "agents": ["agent_id1", "agent_id2"],  // 필요한 에이전트 순서대로 (1~3개)
  "reason": "간단한 라우팅 이유",
  "multi_flow": true/false  // 에이전트 간 결과를 연결해야 하면 true
}

복합 질문(예: "7월 프로모션 전략과 고객 타겟팅")은 multi_flow: true로 여러 에이전트 활성화."""

# ── 사용자 세션 관리 ──────────────────────────────────
user_sessions = {}

def get_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "active_agent": None,
            "history": {a: [] for a in AGENTS},
            "mode": "auto"  # auto | strategy | sales | crm
        }
    return user_sessions[user_id]

# ── 에이전트 호출 ────────────────────────────────────
async def call_agent(agent_id: str, question: str, history: list, context: str = "") -> str:
    agent = AGENTS[agent_id]
    system = agent["prompt"]
    if context:
        system += f"\n\n[이전 에이전트 컨텍스트]\n{context}"

    messages = history[-6:] + [{"role": "user", "content": question}]

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=system,
        messages=messages
    )
    return response.content[0].text

# ── 오케스트레이터: 라우팅 결정 ──────────────────────
async def orchestrate(question: str) -> dict:
    import json
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=ORCHESTRATOR_PROMPT,
        messages=[{"role": "user", "content": question}]
    )
    text = response.content[0].text.strip()
    # JSON 파싱
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except:
            pass
    # 파싱 실패 시 기본값
    return {"agents": ["strategy"], "reason": "기본 라우팅", "multi_flow": False}

# ── 멀티에이전트 플로우 실행 ──────────────────────────
async def run_multi_agent(question: str, session: dict, status_callback) -> str:
    routing = await orchestrate(question)
    agents_to_run = routing.get("agents", ["strategy"])
    multi_flow = routing.get("multi_flow", False)

    results = []
    context = ""

    for i, agent_id in enumerate(agents_to_run):
        if agent_id not in AGENTS:
            continue
        agent = AGENTS[agent_id]

        # 진행 상태 알림
        await status_callback(f"{agent['emoji']} {agent['name']} 분석 중...")

        # 멀티플로우: 이전 에이전트 결과를 컨텍스트로 전달
        if multi_flow and i > 0 and results:
            context = f"앞선 에이전트 분석 결과:\n{results[-1]['answer'][:800]}"

        answer = await call_agent(
            agent_id,
            question,
            session["history"][agent_id],
            context
        )

        # 히스토리 저장 (최근 10턴)
        session["history"][agent_id].append({"role": "user", "content": question})
        session["history"][agent_id].append({"role": "assistant", "content": answer})
        if len(session["history"][agent_id]) > 20:
            session["history"][agent_id] = session["history"][agent_id][-20:]

        results.append({"agent": agent_id, "name": agent["name"], "answer": answer})

    # 결과 포맷팅
    if len(results) == 1:
        return f"{results[0]['name']}\n\n{results[0]['answer']}"
    else:
        combined = ""
        for r in results:
            combined += f"{'─'*30}\n{r['name']}\n{'─'*30}\n{r['answer']}\n\n"
        return combined.strip()

# ── 텔레그램 핸들러 ──────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ 접근 권한이 없습니다.")
        return

    keyboard = [
        [InlineKeyboardButton("⚡ 전략", callback_data="mode_strategy"),
         InlineKeyboardButton("📋 영업기획", callback_data="mode_sales"),
         InlineKeyboardButton("👥 CRM", callback_data="mode_crm")],
        [InlineKeyboardButton("🤖 자동 라우팅", callback_data="mode_auto")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🏢 *MCP HUB — S\\&M 본부 에이전트*\n\n"
        "에이전트를 선택하거나 자동 라우팅으로 질문하세요\\.\n\n"
        "현재 모드: 🤖 자동 라우팅",
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)

    mode_map = {
        "mode_auto": ("auto", "🤖 자동 라우팅"),
        "mode_strategy": ("strategy", "⚡ 전략 에이전트"),
        "mode_sales": ("sales", "📋 영업기획 에이전트"),
        "mode_crm": ("crm", "👥 CRM 에이전트")
    }

    if query.data in mode_map:
        mode, name = mode_map[query.data]
        session["mode"] = mode
        await query.edit_message_text(
            f"✅ *{name}* 모드로 전환됐습니다\\.\n질문을 입력하세요\\.",
            parse_mode="MarkdownV2"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        return

    question = update.message.text
    session = get_session(user_id)

    # 타이핑 인디케이터
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    # 상태 메시지
    status_msg = await update.message.reply_text("🔄 분석 중...")

    async def update_status(text):
        try:
            await status_msg.edit_text(text)
        except:
            pass

    try:
        mode = session.get("mode", "auto")

        if mode == "auto":
            # 오케스트레이터가 자동 라우팅
            answer = await run_multi_agent(question, session, update_status)
        else:
            # 특정 에이전트 직접 호출
            await update_status(f"{AGENTS[mode]['emoji']} {AGENTS[mode]['name']} 분석 중...")
            answer = await call_agent(
                mode, question,
                session["history"][mode]
            )
            session["history"][mode].append({"role": "user", "content": question})
            session["history"][mode].append({"role": "assistant", "content": answer})
            answer = f"{AGENTS[mode]['name']}\n\n{answer}"

        # 상태 메시지 삭제 후 답변 전송 (4096자 제한 분할)
        await status_msg.delete()

        # 텔레그램 4096자 제한 처리
        if len(answer) <= 4096:
            await update.message.reply_text(answer)
        else:
            chunks = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
            for i, chunk in enumerate(chunks):
                prefix = f"[{i+1}/{len(chunks)}]\n" if len(chunks) > 1 else ""
                await update.message.reply_text(prefix + chunk)

    except Exception as e:
        await status_msg.edit_text(f"⚠️ 오류가 발생했습니다: {str(e)}")
        logger.error(f"Error: {e}", exc_info=True)

async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """에이전트 전환 명령어 처리"""
    user_id = update.effective_user.id
    session = get_session(user_id)
    cmd = update.message.text.split()[0][1:]  # /전략 → 전략

    cmd_map = {"전략": "strategy", "영업": "sales", "crm": "crm", "자동": "auto"}
    if cmd in cmd_map:
        session["mode"] = cmd_map[cmd]
        names = {"strategy": "⚡ 전략", "sales": "📋 영업기획", "crm": "👥 CRM", "auto": "🤖 자동"}
        await update.message.reply_text(f"✅ {names[cmd_map[cmd]]} 모드로 전환됐습니다.")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """대화 히스토리 초기화"""
    user_id = update.effective_user.id
    if user_id in user_sessions:
        user_sessions[user_id]["history"] = {a: [] for a in AGENTS}
    await update.message.reply_text("🗑️ 대화 히스토리가 초기화됐습니다.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """현재 상태 확인"""
    user_id = update.effective_user.id
    session = get_session(user_id)
    mode = session.get("mode", "auto")
    mode_names = {"auto": "🤖 자동 라우팅", "strategy": "⚡ 전략", "sales": "📋 영업기획", "crm": "👥 CRM"}

    history_counts = {k: len(v)//2 for k, v in session["history"].items()}
    status = (
        f"📊 현재 상태\n\n"
        f"모드: {mode_names.get(mode, mode)}\n\n"
        f"대화 히스토리:\n"
        f"  ⚡ 전략: {history_counts['strategy']}턴\n"
        f"  📋 영업기획: {history_counts['sales']}턴\n"
        f"  👥 CRM: {history_counts['crm']}턴"
    )
    await update.message.reply_text(status)

# ── 메인 ────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # 커맨드 핸들러
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("전략", agent_command))
    app.add_handler(CommandHandler("영업", agent_command))
    app.add_handler(CommandHandler("crm", agent_command))
    app.add_handler(CommandHandler("자동", agent_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("status", status_command))

    # 버튼 콜백
    app.add_handler(CallbackQueryHandler(button_callback))

    # 일반 메시지
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 MCP HUB 텔레그램 봇 시작...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
