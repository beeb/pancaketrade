from typing import List, NamedTuple

from pancaketrade.network import Network
from pancaketrade.utils.config import Config
from pancaketrade.utils.db import remove_token
from pancaketrade.utils.generic import check_chat_id
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, ConversationHandler


class RemoveTokenResponses(NamedTuple):
    TOKENCHOICE: int = 0


class RemoveTokenConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = RemoveTokenResponses()
        self.handler = ConversationHandler(
            entry_points=[CommandHandler('removetoken', self.command_removetoken)],
            states={self.next.TOKENCHOICE: [CallbackQueryHandler(self.command_removetoken_tokenchoice)]},
            fallbacks=[CommandHandler('cancelremovetoken', self.command_cancelremovetoken)],
            name='removetoken_conversation',
            persistent=True,
            conversation_timeout=60,
        )

    @check_chat_id
    def command_removetoken(self, update: Update, _: CallbackContext):
        assert update.message
        buttons: List[InlineKeyboardButton] = []
        for token in sorted(self.parent.watchers.values(), key=lambda token: token.symbol.lower()):
            buttons.append(InlineKeyboardButton(token.name, callback_data=token.address))
        buttons_layout = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]  # noqa: E203
        buttons_layout.append([InlineKeyboardButton('❌ Cancel', callback_data='cancel')])
        reply_markup = InlineKeyboardMarkup(buttons_layout)
        update.message.reply_html('Choose the token to remove from the list below.', reply_markup=reply_markup)
        return self.next.TOKENCHOICE

    @check_chat_id
    def command_removetoken_tokenchoice(self, update: Update, _: CallbackContext):
        assert update.callback_query
        query = update.callback_query
        query.answer()
        if query.data == 'cancel':
            query.edit_message_text('⚠️ OK, I\'m cancelling this command.')
            return ConversationHandler.END
        remove_token(self.parent.watchers[query.data].token_record)
        token_name = self.parent.watchers[query.data].name
        del self.parent.watchers[query.data]
        query.edit_message_text(f'✅ Alright, the token <b>"{token_name}"</b> was removed.')
        return ConversationHandler.END

    @check_chat_id
    def command_cancelremovetoken(self, update: Update, context: CallbackContext):
        assert update.effective_chat
        context.bot.send_message(chat_id=update.effective_chat.id, text='⚠️ OK, I\'m cancelling this command.')
        return ConversationHandler.END
