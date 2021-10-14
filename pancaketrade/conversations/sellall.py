from decimal import Decimal
from typing import NamedTuple

from loguru import logger
from pancaketrade.network import Network
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import chat_message, check_chat_id, format_token_amount
from pancaketrade.watchers import TokenWatcher
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, ConversationHandler
from web3 import Web3


class SellAllResponses(NamedTuple):
    CONFIRM: int = 0


class SellAllConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = SellAllResponses()
        self.handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.command_sellall, pattern='^sellall:0x[a-fA-F0-9]{40}$')],
            states={
                self.next.CONFIRM: [CallbackQueryHandler(self.command_sellall_confirm, pattern='^[^:]*$')],
            },
            fallbacks=[CommandHandler('cancel', self.command_cancelsell)],
            name='sellall_conversation',
        )

    @check_chat_id
    def command_sellall(self, update: Update, context: CallbackContext):
        assert update.callback_query
        query = update.callback_query
        assert query.data
        token_address = query.data.split(':')[1]
        if not Web3.isChecksumAddress(token_address):
            chat_message(update, context, text='⛔️ Invalid token address.', edit=False)
            return ConversationHandler.END
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
            edit=self.config.update_messages,
        )
        return self.next.CONFIRM

    @check_chat_id
    def command_sellall_confirm(self, update: Update, context: CallbackContext):
        assert update.callback_query
        query = update.callback_query
        if query.data == 'cancel':
            chat_message(update, context, text='⚠️ OK, I\'m cancelling this command.', edit=self.config.update_messages)
            return ConversationHandler.END
        if not Web3.isChecksumAddress(query.data):
            chat_message(update, context, text='⛔️ Invalid token address.', edit=self.config.update_messages)
            return ConversationHandler.END
        token: TokenWatcher = self.parent.watchers[query.data]
        if not self.net.is_approved(token_address=token.address):
            # when selling we require that the token is approved on pcs beforehand
            logger.info(f'Need to approve {token.symbol} for trading on PancakeSwap.')
            chat_message(
                update,
                context,
                text=f'Approving {token.symbol} for trading on PancakeSwap...',
                edit=self.config.update_messages,
            )
            res = self.net.approve(token_address=token.address)
            if res:
                chat_message(update, context, text='✅ Approval successful!', edit=self.config.update_messages)
            else:
                chat_message(update, context, text='⛔ Approval failed', edit=False)
                return ConversationHandler.END
        balance_tokens = self.net.get_token_balance_wei(token_address=token.address)
        balance_decimal = Decimal(balance_tokens) / Decimal(10 ** token.decimals)
        chat_message(
            update,
            context,
            text=f'Selling {format_token_amount(balance_decimal)} {token.symbol}...',
            edit=self.config.update_messages,
        )
        res, bnb_out, txhash_or_error = self.net.sell_tokens(
            token.address,
            amount_tokens=balance_tokens,
            slippage_percent=token.default_slippage,
            gas_price='+20.1',
        )
        if not res:
            logger.error(f'Transaction failed: {txhash_or_error}')
            if len(txhash_or_error) == 66:
                reason_or_link = f'<a href="https://bscscan.com/tx/{txhash_or_error}">{txhash_or_error[:8]}...</a>'
            else:
                reason_or_link = txhash_or_error
            chat_message(
                update, context, text=f'⛔️ Transaction failed: {reason_or_link}', edit=self.config.update_messages
            )
            return ConversationHandler.END
        logger.success(f'Sell transaction succeeded. Received {bnb_out:.3g} BNB')
        usd_out = self.net.get_bnb_price() * bnb_out
        chat_message(
            update,
            context,
            text=f'✅ Got {bnb_out:.3g} BNB (${usd_out:.2f}) at '
            + f'tx <a href="https://bscscan.com/tx/{txhash_or_error}">{txhash_or_error[:8]}...</a>',
            edit=self.config.update_messages,
        )
        if len(token.orders) > 0:
            chat_message(
                update,
                context,
                text=f'⚠️ You still have pending orders for {token.name}. '
                + 'Please delete them in case they are not relevant anymore.',
                edit=False,
            )
        return ConversationHandler.END

    @check_chat_id
    def command_cancelsell(self, update: Update, context: CallbackContext):
        chat_message(update, context, text='⚠️ OK, I\'m cancelling this command.', edit=self.config.update_messages)
        return ConversationHandler.END
