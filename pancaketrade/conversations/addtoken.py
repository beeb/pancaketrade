from typing import NamedTuple

from pancaketrade.utils.generic import check_chat_id
from telegram import Update
from telegram.ext import CallbackContext, ConversationHandler


class AddTokenResponses(NamedTuple):
    ADD_TOKEN_ADDRESS: int = 0


class AddTokenConversation:
    def __init__(self):
        self.next = AddTokenResponses()

    @check_chat_id
    def command_addtoken(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        update.message.reply_html('Please send me the token contract address.')
        return self.next.ADD_TOKEN_ADDRESS

    @check_chat_id
    def command_addtoken_address(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat

    @check_chat_id
    def command_canceltoken(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        update.message.reply_html('OK, I\'m cancelling this command.')
        return ConversationHandler.END
