"""Deliver and answer notification prompts with the existing Telegram bot."""

import json
import logging
from urllib.parse import urlsplit

from sqlalchemy import select, update

from agent.modules.notifications.interruption import ResponseConflict, active_manager, identities_query
from agent.modules.notifications.models import InterruptionRecord, NotificationDelivery
from agent.modules.notifications.settings import messages
from agent.modules.users.models import UserIdentity

PREFIX = "kn:"
PAGE_SIZE = 8
logger = logging.getLogger(__name__)


def _direct(questions):
    return (len(questions) == 1 and questions[0].get("selection_mode") != "multiple"
            and (not questions[0]["options"] or not questions[0]["free_text"].get("required")))


def _required_text_only(questions):
    return all(question["required"] and question["free_text"]["enabled"] and not question["options"]
               for question in questions)


def _keyboard(delivery, question, index, url, *, selected=(), page=0, submit=None):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    prefix = f"{PREFIX}{delivery.token}:{index}:"
    if question:
        options = question["options"]
        for number in range(page * PAGE_SIZE, min(len(options), (page + 1) * PAGE_SIZE)):
            option = options[number]
            mark = "✓ " if option["id"] in selected else ""
            rows.append([InlineKeyboardButton(text=(mark + option["label"])[:64], callback_data=f"{prefix}{number}")])
        pages = []
        if page > 0:
            pages.append(InlineKeyboardButton(text="‹", callback_data=f"{prefix}p{page - 1}"))
        if (page + 1) * PAGE_SIZE < len(options):
            pages.append(InlineKeyboardButton(text="›", callback_data=f"{prefix}p{page + 1}"))
        if pages:
            rows.append(pages)
    if submit:
        rows.append([InlineKeyboardButton(text=submit[:64], callback_data=f"{prefix}s")])
    # Telegram rejects HTTP URL buttons with single-label hostnames such as localhost.
    hostname = urlsplit(url).hostname or ""
    if "." in hostname or ":" in hostname:
        rows.append([InlineKeyboardButton(text=messages()["dashboard"], url=url)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def send_request(manager, delivery, row, external_id, url):
    from agent.modules.channels.telegram.adapter import get_telegram_adapter

    bot = get_telegram_adapter().bot
    if bot is None:
        return False
    request = json.loads(row.request_json)
    questions = request["questions"]
    direct = _direct(questions)
    targets = json.loads(delivery.telegram_json)
    if targets.get("bot_id") != bot.id:
        targets = {"bot_id": bot.id, "messages": {}}
    for index, question in enumerate(questions):
        if str(index) in targets["messages"]:
            continue
        prefix = delivery.text + "\n\n" if index == 0 else ""
        suffix = "\n\n" + messages()["reply"] if question["free_text"]["enabled"] else ""
        submit = _submit_label(request, index, direct)
        body = (prefix + question["question"])[:1500] + suffix
        markup = _keyboard(delivery, question, index, url, submit=submit)
        if markup is None and question["free_text"]["enabled"]:
            from aiogram.types import ForceReply

            markup = ForceReply(force_reply=True)
        sent = await bot.send_message(chat_id=external_id, text=body, parse_mode=None, reply_markup=markup)
        targets["messages"][str(index)] = [sent.chat.id, sent.message_id]
        async with await manager.session_factory() as session, session.begin():
            await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.id == delivery.id, NotificationDelivery.attempts == delivery.attempts,
            ).values(telegram_json=json.dumps(targets)))
    return True


async def _prompt(manager, *, bot_id, chat_id, message_id, external_id, token=None):
    async with await manager.session_factory() as session:
        if token:
            query = select(NotificationDelivery).where(NotificationDelivery.token == token)
        else:
            query = (select(NotificationDelivery).join(UserIdentity, NotificationDelivery.identity_id == UserIdentity.id)
                     .where(NotificationDelivery.token.is_not(None), UserIdentity.platform == "telegram",
                            UserIdentity.external_id == external_id))
        for delivery in (await session.scalars(query)).all():
            targets = json.loads(delivery.telegram_json)
            if targets.get("bot_id") != bot_id:
                continue
            for index, target in targets["messages"].items():
                if target != [chat_id, message_id]:
                    continue
                identity = await session.scalar(identities_query().where(
                    UserIdentity.id == delivery.identity_id, UserIdentity.user_id == delivery.user_id,
                    UserIdentity.platform == "telegram", UserIdentity.external_id == external_id,
                ))
                row = await session.scalar(select(InterruptionRecord).where(
                    InterruptionRecord.key == delivery.interruption_key))
                return delivery, row, int(index), bool(identity and identity.id in manager.settings.recipient_identity_ids)
    return None


async def _pending_text_prompts(manager, *, bot_id, chat_id, external_id):
    recipient_ids = manager.settings.recipient_identity_ids
    if not recipient_ids:
        return []
    recipient = identities_query().where(
        UserIdentity.id == NotificationDelivery.identity_id,
        UserIdentity.user_id == NotificationDelivery.user_id,
        UserIdentity.platform == "telegram",
        UserIdentity.external_id == external_id,
        UserIdentity.id.in_(recipient_ids),
    ).exists()
    query = select(NotificationDelivery, InterruptionRecord).join(
        InterruptionRecord, InterruptionRecord.key == NotificationDelivery.interruption_key,
    ).where(
        recipient,
        NotificationDelivery.event_type == "ask_user",
        NotificationDelivery.token.is_not(None),
        InterruptionRecord.status == "waiting",
        InterruptionRecord.deadline > manager.clock(),
    )
    async with await manager.session_factory() as session:
        deliveries = (await session.execute(query)).all()

    prompts = []
    for delivery, row in deliveries:
        targets = json.loads(delivery.telegram_json)
        if targets.get("bot_id") != bot_id:
            continue
        questions = json.loads(row.request_json)["questions"]
        sent_messages = targets["messages"]
        indices = [
            index for index, question in enumerate(questions)
            if question["free_text"]["enabled"] and str(index) in sent_messages
            and sent_messages[str(index)][0] == chat_id
        ]
        if indices and await manager.is_current(row):
            prompts.extend((delivery, row, index, True) for index in indices)
    return prompts


async def _change_draft(manager, delivery, question, *, choice=None, reply=None):
    for _ in range(5):
        async with await manager.session_factory() as session, session.begin():
            stored = await session.scalar(select(NotificationDelivery.draft_json).where(
                NotificationDelivery.id == delivery.id))
            draft = json.loads(stored)
            answer = draft.setdefault(question["id"], {
                "question_id": question["id"], "selected_option_ids": [], "custom_text": "",
            })
            selected = answer["selected_option_ids"]
            if reply is not None:
                answer["custom_text"] = reply
            elif question["selection_mode"] == "single":
                selected[:] = [choice]
            elif choice in selected:
                selected.remove(choice)
            else:
                selected.append(choice)
            result = await session.execute(update(NotificationDelivery).where(
                NotificationDelivery.id == delivery.id, NotificationDelivery.draft_json == stored,
            ).values(draft_json=json.dumps(draft)))
            if result.rowcount:
                return draft
    raise RuntimeError("The Telegram draft changed concurrently.")


async def _apply(manager, prompt, external_id, *, action="", reply=None):
    delivery, row, index, authorized = prompt
    if not authorized:
        return "unauthorized", None, None
    if row is None or row.status != "waiting" or row.deadline <= manager.clock():
        return "closed", None, None
    request = json.loads(row.request_json)
    questions = request["questions"]
    question = questions[index]
    direct = _direct(questions)
    choice = None
    if reply is not None:
        if not question["free_text"]["enabled"] or not reply:
            return "incomplete", None, None
    elif action != "s":
        if len(action) > 9 or not action.lstrip("p").isdigit():
            return "closed", None, None
        number = int(action.lstrip("p"))
        if action.startswith("p"):
            if number * PAGE_SIZE >= len(question["options"]):
                return "closed", None, None
            selected = json.loads(delivery.draft_json).get(question["id"], {}).get("selected_option_ids", [])
            markup = _keyboard(delivery, question, index, manager.link(delivery, row), selected=selected,
                               page=number, submit=_submit_label(request, index, direct))
            return "saved", None, markup
        if number >= len(question["options"]):
            return "closed", None, None
        choice = question["options"][number]["id"]

    if action == "s" and not direct and index == len(questions) - 1:
        draft = json.loads(delivery.draft_json)
    elif direct and action != "s":
        draft = {question["id"]: {"question_id": question["id"], "selected_option_ids": [choice] if choice else [],
                                  "custom_text": reply or ""}}
    elif action != "s":
        draft = await _change_draft(manager, delivery, question, choice=choice, reply=reply)
        if reply is None:
            selected = draft[question["id"]]["selected_option_ids"]
            markup = _keyboard(delivery, question, index, manager.link(delivery, row), selected=selected,
                               page=number // PAGE_SIZE, submit=_submit_label(request, index, direct))
            return "saved", None, markup
        if not _required_text_only(questions) or not all(
            draft.get(item["id"], {}).get("custom_text", "").strip() for item in questions
        ):
            return "saved", None, None
    else:
        return "closed", None, None

    labels = [option["label"] for item in questions for option in item["options"]
              if option["id"] in draft.get(item["id"], {}).get("selected_option_ids", [])]
    try:
        await manager.accept(row, {"action": "answer", "answers": list(draft.values()), "summary": ", ".join(labels)},
                             identity_id=delivery.identity_id, external_id=external_id, paired_user_id=delivery.user_id)
    except ResponseConflict:
        return "closed", None, None
    except ValueError:
        return "incomplete", None, None
    except PermissionError:
        return "unauthorized", None, None
    confirmation = (messages()["selected"].replace("{selection}", ", ".join(labels)[:1500])
                    if labels else messages()["accepted"])
    return "accepted", confirmation, _keyboard(delivery, None, index, manager.link(delivery, row))


def _submit_label(request, index, direct):
    questions = request["questions"]
    if not direct and index == len(questions) - 1 and not _required_text_only(questions):
        return request.get("submit_label") or messages()["submit"]
    return None


async def handle_callback(query):
    from aiogram.exceptions import TelegramAPIError

    manager = active_manager()
    message = query.message
    if manager is None or message is None or message.chat.type != "private":
        await query.answer(messages()["unauthorized"])
        return
    _, token, index, action = (query.data.split(":") + ["", "", ""])[:4]
    try:
        external_id = str(query.from_user.id)
        prompt = await _prompt(manager, bot_id=query.bot.id, chat_id=message.chat.id,
                               message_id=message.message_id, external_id=external_id, token=token)
        if not prompt or str(prompt[2]) != index:
            await query.answer(messages()["unauthorized"])
            return
        status, confirmation, markup = await _apply(manager, prompt, external_id, action=action)
    except Exception:
        await query.answer(messages()["unavailable"])
        raise
    await query.answer(messages()[status])
    try:
        if confirmation:
            await message.edit_text(confirmation, reply_markup=markup, parse_mode=None)
        elif markup and message.reply_markup != markup:
            await message.edit_reply_markup(reply_markup=markup)
    except TelegramAPIError as exc:
        logger.warning("Could not update Telegram notification message %s: %s", message.message_id, exc)


async def handle_reply(message):
    manager = active_manager()
    if manager is None or message.chat.type != "private" or not message.text:
        return False

    original = message.reply_to_message
    text = message.text.strip()
    if text.startswith("/"):
        if original is None:
            return False
        from agent.modules.channels.commands import get_default_command_registry

        registry = get_default_command_registry()
        command = registry.parse(text)
        if command is not None and registry.get(command.name) is not None:
            return False

    bot_id = message.bot.id
    chat_id = message.chat.id
    external_id = str(message.from_user.id)
    if original is not None:
        if not original.from_user or original.from_user.id != bot_id:
            return False
        prompt = await _prompt(manager, bot_id=bot_id, chat_id=chat_id,
                               message_id=original.message_id, external_id=external_id)
    else:
        if not text:
            return False
        prompts = await _pending_text_prompts(manager, bot_id=bot_id, chat_id=chat_id, external_id=external_id)
        if len(prompts) > 1:
            await message.answer(messages()["ambiguous_reply"], parse_mode=None)
            return True
        prompt = prompts[0] if prompts else None
    if prompt is None:
        return False
    status, confirmation, markup = await _apply(manager, prompt, external_id, reply=text)
    # Force-reply prompts need a separate acknowledgement.
    if confirmation and original is not None and original.reply_markup is not None:
        await original.edit_text(confirmation, reply_markup=markup, parse_mode=None)
    else:
        await message.answer(confirmation or messages()[status], parse_mode=None)
    return True
