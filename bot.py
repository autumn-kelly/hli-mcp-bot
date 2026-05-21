"""
JURA/CASO MCP 멀티에이전트 텔레그램 봇 (노션 저장 기능 포함)
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
NOTION_PAGE_ID = "36705f2f16b0817b84c0da25385f5b64"  # MCP HUB 페이지

# 에이전트별 노션 하위 페이지 ID
NOTION_AGENT_PAGES = {
    "strategy": "36705f2f16b081399f5fe376a882d49e",
    "sales": "36705f2f16b08159a3d5f7f35e2ede42",
    "crm": "36705f2f16b081689c9ed98f9993c78a",
    "auto": "36705f2f16b0817b84c0da25385f5b64"
}

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
사용자의 질문을 분석하여 어떤 에이전트가 처리해야 할지 판단합니다.

에이전트 역할:
- strategy: 시장분석, 경쟁사, 브랜드 전략, CEO 보고, 채널 전략
- sales: 판촉 기획, 영업 목표, 프로모션, B2B 영업, 채널 관리
- crm: 고객 세그먼트, LTV, 리텐션, 캠페인, 소모품/업그레이드 사이클

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

# ── 노션 저장 함수 ──────────────────────────────────
async def save_to_notion(agent_id: str, question: str, answer: str) -> bool:
    if not NOTION_TOKEN:
        return False

    now = datetime.now()
    date_str = now.strftime("%Y.%m.%d %H:%M")
    agent_name = AGENTS.get(agent_id, {}).get("name", "에이전트")
    title = f"{agent_name} — {date_str}"
    parent_id = NOTION_AGENT_PAGES.get(agent_id, NOTION_PAGE_ID)

    # 노션 페이지 생성
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # 답변을 2000자 단위로 분할 (노션 블록 제한)
    def split_text(text, max_len=1800):
        return [text[i:i+max_len] for i in range(0, len(text), max_len)]

    answer_blocks = []
    for chunk in split_text(answer):
        answer_blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": chunk}}]
            }
        })

    payload = {
        "parent": {"page_id": parent_id},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        },
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "질문"}}]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": question}}]
                }
            },
            {
                "object": "block",
                "type": "divider",
                "divider": {}
            },
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "답변"}}]
                }
            },
            *answer_blocks
        ]
    }

    async with httpx.AsyncClient() as client_http:
        response = await client_http.post(url, headers=headers, json=payload, timeout=30)
        return response.status_code == 200

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

    # 마지막 에이전트 저장 (노션 저장 시 사용)
    if results:
        session["last_agent"] = results[-1]["agent"]

    if len(results) == 1:
        return f"{results[0]['name']}\n\n{results[0]['answer']}"
    else:
        combined = ""
        for r in results:
            combined += f"{'─'*28}\n{r['name']}\n{'─'*28}\n{r['answer']}\n\n"
        return combined.strip()

# ── 텔레그램 핸들러 ──────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("접근 권한이 없습니다.")
        return
    keyboard = [
        [InlineKeyboardButton("⚡ 전략", callback_data="mode_strategy"),
         InlineKeyboardButton("📋 영업기획", callback_data="mode_sales"),
         InlineKeyboardButton("👥 CRM", callback_data="mode_crm")],
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
        "mode_crm": ("crm", "👥 CRM 에이전트")
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

        # 마지막 대화 저장
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

        # 저장 안내
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
            await msg.edit_text("✅ 노션에 저장됐습니다!")
        else:
            await msg.edit_text("⚠️ 저장에 실패했습니다. NOTION_TOKEN을 확인해 주세요.")
    except Exception as e:
        await msg.edit_text(f"⚠️ 저장 오류: {str(e)}")
        logger.error(f"Notion save error: {e}", exc_info=True)

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    cmd = update.message.text.split()[0][1:]
    cmd_map = {"strategy": "strategy", "sales": "sales", "crm": "crm", "auto": "auto"}
    names = {"strategy": "⚡ 전략", "sales": "📋 영업기획", "crm": "👥 CRM", "auto": "🤖 자동"}
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
    names = {"auto": "🤖 자동", "strategy": "⚡ 전략", "sales": "📋 영업기획", "crm": "👥 CRM"}
    counts = {k: len(v)//2 for k, v in session["history"].items()}
    await update.message.reply_text(
        f"현재 모드: {names.get(mode, mode)}\n\n"
        f"대화 히스토리:\n"
        f"  ⚡ 전략: {counts['strategy']}턴\n"
        f"  📋 영업기획: {counts['sales']}턴\n"
        f"  👥 CRM: {counts['crm']}턴\n\n"
        f"노션 연동: {'✅ 활성' if NOTION_TOKEN else '❌ 미설정'}"
    )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("save", save_command))
    app.add_handler(CommandHandler("strategy", mode_command))
    app.add_handler(CommandHandler("sales", mode_command))
    app.add_handler(CommandHandler("crm", mode_command))
    app.add_handler(CommandHandler("auto", mode_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("MCP HUB 텔레그램 봇 시작...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
