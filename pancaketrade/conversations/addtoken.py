from typing import NamedTuple
from web3 import Web3

from pancaketrade.utils.generic import check_chat_id
from pancaketrade.bot import TradeBot
from telegram import Update
from telegram.ext import CallbackContext, ConversationHandler, CommandHandler, Filters, MessageHandler


class AddTokenResponses(NamedTuple):
    ADD_TOKEN_ADDRESS: int = 0


class AddTokenConversation:
    def __init__(self, parent: TradeBot):
        self.parent = parent
        self.next = AddTokenResponses()
        self.handler = ConversationHandler(
            entry_points=[CommandHandler('addtoken', self.command_addtoken)],
            states={
                self.next.ADD_TOKEN_ADDRESS: [
                    MessageHandler(Filters.text & ~Filters.command, self.command_addtoken_address)
                ]
            },
            fallbacks=[CommandHandler('canceltoken', self.command_canceltoken)],
            name='addtoken_conversation',
            persistent=True,
        )

    @check_chat_id
    def command_addtoken(self, update: Update, _: CallbackContext):
        assert update.message and update.effective_chat
        update.message.reply_html('Please send me the token contract address.')
        return self.next.ADD_TOKEN_ADDRESS

    @check_chat_id
    def command_addtoken_address(self, update: Update, context: CallbackContext):
        assert update.message and update.message.text and update.effective_chat and context.user_data
        context.user_data.clear()
        response = update.message.text.strip()
        if Web3.isAddress(response):
            token_address = Web3.toChecksumAddress(response)
        else:
            update.message.reply_html('The address you provided is not a valid ETH address. Try again:')
            return self.next.ADD_TOKEN_ADDRESS
        context.user_data['decimals'] = self.parent.net.get_token_decimals(token_address)
        context.user_data['symbol'] = self.parent.net.get_token_symbol(token_address)

    @check_chat_id
    def command_canceltoken(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat and context.user_data
        context.user_data.clear()
        update.message.reply_html('OK, I\'m cancelling this command.')
        return ConversationHandler.END
