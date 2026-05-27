"""
JURA/CASO MCP 멀티에이전트 텔레그램 봇 (노션 저장 + 마케팅 에이전트 포함)
"""

import os
import logging
import json
import httpx
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import anthropic

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")

# 노션 페이지 ID (MCP HUB 하위 페이지들 - 새로 생성)
NOTION_PAGES = {
    "strategy": "36705f2f-16b0-81c7-a093-e5f0c8de7b0f",
    "sales":    "36705f2f-16b0-8160-9f8c-d0c5367f4cab",
    "crm":      "36705f2f-16b0-8172-bbf4-f864435dba94",
    "marketing":"36705f2f-16b0-818c-a81c-c17b7ef3080e",
    "auto":     "36705f2f-16b0-817b-84c0-da25385f5b64"
}

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 공통 룰 ──────────────────────────────────────────
COMMON_RULES = """
## 공통 원칙

**절대 하지 말 것**
- 근거 없는 제안 및 낙관적 전망 금지
- 이론적이고 실행 불가능한 제안 금지
- 인력 채용을 당연한 해결책으로 제시 금지
- 전략적 근거 없는 무분별한 할인·가격 경쟁 금지
- 구매 후 캐시백 방식의 프로모션 금지 (프리미엄 이미지 훼손)
- 기존 맥락 무시한 원론적 제안 금지
- 해외·대기업 사례 직접 인용 추천 금지
- 단순 포인트/캐시백 지급 방식의 리뷰 이벤트 금지 (차별화된 방식으로 제안할 것)

**허용되는 프로모션**
- 전략적 근거(재고 소진, 신제품 런칭, 시즌 특수 등)가 명확한 경우의 할인
- 증정·번들·체험 방식의 프로모션 (가치 기반)
- 리뷰 이벤트는 단순 캐시백 대신 차별화된 방식 제안 (예: 프라이빗 클래스 초청, 전문가 1:1 상담권, 한정 액세서리 증정 등)

**브랜드 원칙**
- 질문에서 JURA 또는 CASO DESIGN을 파악해 해당 브랜드 집중
- 브랜드가 명시되지 않으면 두 브랜드 모두 검토

**톤 원칙**
- 본부장(CSO) 관점 중심으로 답변
- 실행 계획은 팀장이 팀원에게 지시할 수 있는 수준으로 보완

**시기 원칙**
- 현재 날짜 기준으로 실행 시기 제안
- 현재 월 말(25일 이후)에 해당 월 시작을 제안하는 경우 다음 달로 조정
- 예: 5월 25일 이후 "5월부터 실행" 제안 → "6월부터 실행"으로 수정

**답변 형식 (반드시 포함)**
1. 분석 및 제안 본문 (중간 분량)
2. 실행 액션 우선순위 (반드시 1~3개로 압축, 그 이상 나열 금지)
   - 1순위: 즉시 실행 가능한 것
   - 2순위: 2~4주 내
   - 3순위: 1개월 내
3. 리스크 체크 (2~3개 핵심만 간결하게)
"""

AGENTS = {
    "strategy": {
        "name": "⚡ 전략 에이전트", "emoji": "⚡",
        "prompt": """당신은 JURA/CASO DESIGN 세일즈&마케팅 본부 전용 전략 에이전트입니다.
본부장(CSO)의 의사결정을 지원하는 참모 역할이며 팀에 직접 노출되지 않습니다.

## 역할
- 시장 인텔리전스 및 경쟁사 분석
- 브랜드 포지셔닝 및 채널 전략 제언
- CEO 보고용 전략 인사이트 생산
- 영업기획·CRM·마케팅팀에 전달할 전략 방향 정의

## 브랜드 컨텍스트
- JURA: 스위스 프리미엄 전자동 커피머신, 100만~1000만원대, 고관여·감성 소구, B2C + 오피스/카페 B2B
- CASO DESIGN: 혁신 주방가전, 20만~80만원대, 라이프스타일·디자인 소구, MZ 홈리빙 타겟

""" + COMMON_RULES
    },
    "sales": {
        "name": "📋 영업기획 에이전트", "emoji": "📋",
        "prompt": """당신은 JURA/CASO DESIGN 영업기획 에이전트입니다.
본부장 관점의 판단과 함께 팀장 수준의 실행 지시사항을 생성합니다.

## 담당 팀 및 역할
- **B2B 영업팀**: 오피스·호텔·기업 대상 JURA 영업 지시사항 생성
- **도메스틱 영업팀**: 백화점·온라인(네이버 스토어·자사몰) 채널 지시사항 생성
- **카페팀**: 고객 교육, 머신 설치, 고객 접점 관리 실행 플랜 지시사항 생성

## 핵심 원칙
- 가치 기반 판매, 프리미엄 포지셔닝 유지
- 채널별 역할 명확히 구분하여 지시
- 카페팀은 영업보다 고객 관계·교육 중심으로 접근

""" + COMMON_RULES
    },
    "crm": {
        "name": "👥 CRM 에이전트", "emoji": "👥",
        "prompt": """당신은 JURA/CASO DESIGN CRM 에이전트입니다.
본부장 관점의 판단과 함께 마케팅팀·온라인 세일즈팀을 위한 실행 제안을 생성합니다.

## 역할
- 고객 세그먼테이션 및 LTV 분석
- 마케팅팀 캠페인 연계 제안
- 온라인 세일즈(네이버 스토어·자사몰) 프로모션 연계 제안
- JURA 소모품·AS·업그레이드 사이클 기반 고객 접점 설계
- CASO DESIGN 크로스셀·라이프스타일 연계 제안

## 핵심 원칙
- 고객 데이터 기반 제안만 (데이터 없이 추측 금지)
- 캠페인은 온라인 채널 실행 가능한 것 중심
- 프리미엄 고객 경험 훼손하는 프로모션 제안 금지

""" + COMMON_RULES
    },
    "marketing": {
        "name": "📣 마케팅 에이전트", "emoji": "📣",
        "prompt": """당신은 JURA/CASO DESIGN 마케팅 에이전트입니다.
본부장 관점의 판단과 함께 마케팅팀 실행 지시사항을 생성합니다.

## 역할
- 마케팅팀 콘텐츠·캠페인 실행 지시사항 생성
- CRM 실행계획 연계 마케팅 액션 제안
- 영업 프로모션 연계 마케팅 지시사항 생성
- SNS·디지털·오프라인 채널별 실행 방향 제시

## 핵심 원칙
- JURA: 프리미엄 감성·체험 중심 커뮤니케이션
- CASO DESIGN: 혁신·라이프스타일 소구
- 콘텐츠는 재활용 가능한 구조로 기획
- 영업 프로모션과 마케팅 메시지 일관성 유지

""" + COMMON_RULES
    }
}

ORCHESTRATOR_PROMPT = """당신은 JURA/CASO DESIGN 세일즈&마케팅 본부의 AI 오케스트레이터입니다.
사용자의 질문을 분석하여 어떤 에이전트가 처리해야 할지 판단합니다.

에이전트 역할:
- strategy: 시장분석, 경쟁사, 브랜드 전략, CEO 보고, 채널 전략
- sales: 판촉 기획, 영업 목표, 프로모션, B2B 영업, 채널 관리
- crm: 고객 세그먼트, LTV, 리텐션, 캠페인, 소모품/업그레이드 사이클
- marketing: 콘텐츠 기획, SNS, 광고, 브랜드 커뮤니케이션, 영상 기획

다음 JSON 형식으로만 응답하세요:
{"agents": ["agent_id1"], "multi_flow": false}

복합 질문은 여러 에이전트, multi_flow: true 설정."""

user_sessions = {}

def get_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "mode": "auto",
            "history": {a: [] for a in AGENTS},
            "last_question": "",
            "last_answer": "",
            "last_agent": "auto"
        }
    return user_sessions[user_id]

async def save_to_notion(agent_id: str, question: str, answer: str) -> bool:
    if not NOTION_TOKEN:
        return False

    now = datetime.now()
    date_str = now.strftime("%Y.%m.%d %H:%M")
    agent_name = AGENTS.get(agent_id, {}).get("name", "에이전트")
    title = f"{agent_name} — {date_str}"
    parent_id = NOTION_PAGES.get(agent_id, NOTION_PAGES["auto"])

    def split_text(text, max_len=1800):
        return [text[i:i+max_len] for i in range(0, len(text), max_len)]

    answer_blocks = []
    for chunk in split_text(answer):
        answer_blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]}
        })

    payload = {
        "parent": {"page_id": parent_id},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        "children": [
            {"object": "block", "type": "heading_2",
             "heading_2": {"rich_text": [{"type": "text", "text": {"content": "질문"}}]}},
            {"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": [{"type": "text", "text": {"content": question}}]}},
            {"object": "block", "type": "divider", "divider": {}},
            {"object": "block", "type": "heading_2",
             "heading_2": {"rich_text": [{"type": "text", "text": {"content": "답변"}}]}},
            *answer_blocks
        ]
    }

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    async with httpx.AsyncClient() as c:
        r = await c.post("https://api.notion.com/v1/pages", headers=headers, json=payload, timeout=30)
        logger.info(f"Notion save: {r.status_code} - {r.text[:200]}")
        return r.status_code == 200

async def call_agent(agent_id, question, history, context=""):
    agent = AGENTS[agent_id]
    system = agent["prompt"]
    if context:
        system += f"\n\n[이전 에이전트 컨텍스트]\n{context}"
    messages = history[-6:] + [{"role": "user", "content": question}]
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=system,
        messages=messages
    )
    return response.content[0].text

async def orchestrate(question):
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=200,
        system=ORCHESTRATOR_PROMPT,
        messages=[{"role": "user", "content": question}]
    )
    text = response.content[0].text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except:
            pass
    return {"agents": ["strategy"], "multi_flow": False}

async def run_multi_agent(question, session, status_callback):
    routing = await orchestrate(question)
    agents_to_run = routing.get("agents", ["strategy"])
    multi_flow = routing.get("multi_flow", False)
    results = []
    context = ""

    for i, agent_id in enumerate(agents_to_run):
        if agent_id not in AGENTS:
            continue
        agent = AGENTS[agent_id]
        await status_callback(f"{agent['emoji']} {agent['name']} 분석 중...")
        if multi_flow and i > 0 and results:
            context = f"앞선 에이전트 분석:\n{results[-1]['answer'][:600]}"
        answer = await call_agent(agent_id, question, session["history"][agent_id], context)
        session["history"][agent_id].append({"role": "user", "content": question})
        session["history"][agent_id].append({"role": "assistant", "content": answer})
        if len(session["history"][agent_id]) > 20:
            session["history"][agent_id] = session["history"][agent_id][-20:]
        results.append({"agent": agent_id, "name": agent["name"], "answer": answer})

    if results:
        session["last_agent"] = results[-1]["agent"]

    if len(results) == 1:
        return f"{results[0]['name']}\n\n{results[0]['answer']}"
    else:
        combined = ""
        for r in results:
            combined += f"{'─'*28}\n{r['name']}\n{'─'*28}\n{r['answer']}\n\n"
        return combined.strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("접근 권한이 없습니다.")
        return
    keyboard = [
        [InlineKeyboardButton("⚡ 전략", callback_data="mode_strategy"),
         InlineKeyboardButton("📋 영업기획", callback_data="mode_sales")],
        [InlineKeyboardButton("👥 CRM", callback_data="mode_crm"),
         InlineKeyboardButton("📣 마케팅", callback_data="mode_marketing")],
        [InlineKeyboardButton("🤖 자동 라우팅", callback_data="mode_auto")]
    ]
    await update.message.reply_text(
        "🏢 MCP HUB — S&M 본부 에이전트\n\n"
        "에이전트를 선택하거나 자동 라우팅으로 질문하세요.\n\n"
        "현재 모드: 🤖 자동 라우팅\n\n"
        "💡 답변 후 /save 입력하면 노션에 자동 저장됩니다.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session = get_session(query.from_user.id)
    mode_map = {
        "mode_auto": ("auto", "🤖 자동 라우팅"),
        "mode_strategy": ("strategy", "⚡ 전략 에이전트"),
        "mode_sales": ("sales", "📋 영업기획 에이전트"),
        "mode_crm": ("crm", "👥 CRM 에이전트"),
        "mode_marketing": ("marketing", "📣 마케팅 에이전트")
    }
    if query.data in mode_map:
        mode, name = mode_map[query.data]
        session["mode"] = mode
        await query.edit_message_text(f"{name} 모드로 전환됐습니다. 질문을 입력하세요.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        return
    question = update.message.text
    session = get_session(user_id)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    status_msg = await update.message.reply_text("🔄 분석 중...")

    async def update_status(text):
        try:
            await status_msg.edit_text(text)
        except:
            pass

    try:
        mode = session.get("mode", "auto")
        if mode == "auto":
            answer = await run_multi_agent(question, session, update_status)
        else:
            await update_status(f"{AGENTS[mode]['emoji']} {AGENTS[mode]['name']} 분석 중...")
            answer = await call_agent(mode, question, session["history"][mode])
            session["history"][mode].append({"role": "user", "content": question})
            session["history"][mode].append({"role": "assistant", "content": answer})
            session["last_agent"] = mode
            answer = f"{AGENTS[mode]['name']}\n\n{answer}"

        session["last_question"] = question
        session["last_answer"] = answer

        await status_msg.delete()

        if len(answer) <= 4096:
            await update.message.reply_text(answer)
        else:
            chunks = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
            for i, chunk in enumerate(chunks):
                prefix = f"[{i+1}/{len(chunks)}]\n" if len(chunks) > 1 else ""
                await update.message.reply_text(prefix + chunk)

        await update.message.reply_text("📎 노션에 저장하려면 /save 를 입력하세요.")

    except Exception as e:
        await status_msg.edit_text(f"오류가 발생했습니다: {str(e)}")
        logger.error(f"Error: {e}", exc_info=True)

async def save_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    if not session.get("last_question"):
        await update.message.reply_text("저장할 대화 내용이 없습니다. 먼저 질문해 주세요.")
        return

    msg = await update.message.reply_text("📎 노션에 저장 중...")
    try:
        success = await save_to_notion(
            session["last_agent"],
            session["last_question"],
            session["last_answer"]
        )
        if success:
            agent_name = AGENTS.get(session["last_agent"], {}).get("name", "에이전트")
            await msg.edit_text(f"✅ {agent_name} 로그에 저장됐습니다!")
        else:
            await msg.edit_text("⚠️ 저장에 실패했습니다.")
    except Exception as e:
        await msg.edit_text(f"⚠️ 저장 오류: {str(e)}")
        logger.error(f"Notion save error: {e}", exc_info=True)

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    cmd = update.message.text.split()[0][1:]
    cmd_map = {"strategy": "strategy", "sales": "sales", "crm": "crm", "marketing": "marketing", "auto": "auto"}
    names = {"strategy": "⚡ 전략", "sales": "📋 영업기획", "crm": "👥 CRM", "marketing": "📣 마케팅", "auto": "🤖 자동"}
    if cmd in cmd_map:
        session["mode"] = cmd_map[cmd]
        await update.message.reply_text(f"{names[cmd]} 모드로 전환됐습니다.")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        user_sessions[user_id]["history"] = {a: [] for a in AGENTS}
        user_sessions[user_id]["last_question"] = ""
        user_sessions[user_id]["last_answer"] = ""
    await update.message.reply_text("대화 히스토리가 초기화됐습니다.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    mode = session.get("mode", "auto")
    names = {"auto": "🤖 자동", "strategy": "⚡ 전략", "sales": "📋 영업기획", "crm": "👥 CRM", "marketing": "📣 마케팅"}
    counts = {k: len(v)//2 for k, v in session["history"].items()}
    await update.message.reply_text(
        f"현재 모드: {names.get(mode, mode)}\n\n"
        f"대화 히스토리:\n"
        f"  ⚡ 전략: {counts['strategy']}턴\n"
        f"  📋 영업기획: {counts['sales']}턴\n"
        f"  👥 CRM: {counts['crm']}턴\n"
        f"  📣 마케팅: {counts['marketing']}턴\n\n"
        f"노션 연동: {'✅ 활성' if NOTION_TOKEN else '❌ 미설정'}"
    )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("save", save_command))
    app.add_handler(CommandHandler("strategy", mode_command))
    app.add_handler(CommandHandler("sales", mode_command))
    app.add_handler(CommandHandler("crm", mode_command))
    app.add_handler(CommandHandler("marketing", mode_command))
    app.add_handler(CommandHandler("auto", mode_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("MCP HUB 텔레그램 봇 시작...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
