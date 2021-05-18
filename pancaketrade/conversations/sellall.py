from typing import NamedTuple

from pancaketrade.network import Network
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import chat_message, check_chat_id
from pancaketrade.watchers import TokenWatcher
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, ConversationHandler
from web3 import Web3
from web3.types import Wei


class SellAllResponses(NamedTuple):
    CONFIRM: int = 0


class SellAllConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = SellAllResponses()
        self.handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.command_sellall, pattern='^sell_all:0x[a-fA-F0-9]{40}$')],
            states={
                self.next.CONFIRM: [CallbackQueryHandler(self.command_sellall_confirm, pattern='^[^:]*$')],
            },
            fallbacks=[CommandHandler('cancelsell', self.command_cancelsell)],
            name='sellall_conversation',
            conversation_timeout=30,
        )

    @check_chat_id
    def command_sellall(self, update: Update, context: CallbackContext):
        assert update.callback_query
        query = update.callback_query
        # query.answer()
        assert query.data
        token_address = query.data.split(':')[1]
        token: TokenWatcher = self.parent.watchers[token_address]
        chat_message(
            update,
            context,
            text=f'Are you sure you want to sell all balance for {token.name}?',
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton('✅ Confirm', callback_data=token_address),
                        InlineKeyboardButton('❌ Cancel', callback_data='cancel'),
                    ]
                ]
            ),
        )
        return self.next.CONFIRM

    @check_chat_id
    def command_sellall_confirm(self, update: Update, context: CallbackContext):
        assert update.callback_query
        query = update.callback_query
        # query.answer()
        if query.data == 'cancel':
            chat_message(update, context, text='⚠️ OK, I\'m cancelling this command.')
            return ConversationHandler.END
        if not Web3.isChecksumAddress(query.data):
            chat_message(update, context, text='⛔️ Invalid token address.')
            return ConversationHandler.END
        token: TokenWatcher = self.parent.watchers[query.data]
        _, v2 = self.net.get_token_price(token_address=token.address, token_decimals=token.decimals, sell=True)
        token_contract = self.net.get_token_contract(token_address=token.address)
        balance_tokens = Wei(token_contract.functions.balanceOf(self.net.wallet).call())
        res, bnb_out, txhash = self.net.sell_tokens(
            token.address,
            amount_tokens=balance_tokens,
            slippage_percent=token.default_slippage,
            gas_price='+1',
            v2=v2,
        )
        chat_message(update, context, text=f'✅ {token.name}.')
        return ConversationHandler.END

    @check_chat_id
    def command_cancelsell(self, update: Update, context: CallbackContext):
        assert update.effective_chat
        chat_message(update, context, text='⚠️ OK, I\'m cancelling this command.')
        return ConversationHandler.END
