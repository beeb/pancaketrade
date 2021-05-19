from typing import List, NamedTuple

from pancaketrade.network import Network
from pancaketrade.utils.config import Config
from pancaketrade.utils.db import remove_order
from pancaketrade.utils.generic import chat_message, check_chat_id
from pancaketrade.watchers import OrderWatcher, TokenWatcher
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, ConversationHandler
from web3 import Web3


class RemoveOrderResponses(NamedTuple):
    CONFIRM: int = 0
    ORDER: int = 1


class RemoveOrderConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = RemoveOrderResponses()
        self.handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.command_removeorder, pattern='^delete_order:0x[a-fA-F0-9]{40}$')],
            states={
                self.next.CONFIRM: [CallbackQueryHandler(self.command_removeorder_confirm, pattern='^[^:]*$')],
                self.next.ORDER: [CallbackQueryHandler(self.command_removeorder_order, pattern='^[^:]*$')],
            },
            fallbacks=[CommandHandler('cancelorder', self.command_cancelorder)],
            name='removeorder_conversation',
            conversation_timeout=60,
        )

    @check_chat_id
    def command_removeorder(self, update: Update, context: CallbackContext):
        assert update.callback_query and update.effective_chat and context.user_data is not None
        query = update.callback_query
        # query.answer()
        assert query.data
        token_address = query.data.split(':')[1]
        if not Web3.isChecksumAddress(token_address):
            self.command_error(update, context, text='Invalid token address.')
            return ConversationHandler.END
        token: TokenWatcher = self.parent.watchers[token_address]
        context.user_data['removeorder'] = {'token_address': token_address}
        orders = token.orders
        buttons: List[InlineKeyboardButton] = [
            InlineKeyboardButton(
                f'{self.get_type_icon(o)} #{o.order_record.id} - {self.get_type_name(o)}',
                callback_data=o.order_record.id,
            )
            for o in orders
        ]
        buttons_layout = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]  # noqa: E203
        buttons_layout.append([InlineKeyboardButton('❌ Cancel', callback_data='cancel')])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons_layout)
        chat_message(
            update,
            context,
            text=f'Select the order you want to remove for {token.name}.',
            reply_markup=reply_markup,
            edit=False,
        )
        return self.next.CONFIRM

    @check_chat_id
    def command_removeorder_confirm(self, update: Update, context: CallbackContext):
        assert update.callback_query and update.effective_chat and context.user_data is not None
        query = update.callback_query
        # query.answer()
        if query.data == 'cancel':
            self.cancel_command(update, context)
            return ConversationHandler.END
        assert query.data
        if not query.data.isdecimal():
            self.command_error(update, context, text='Invalid order ID')
            return ConversationHandler.END
        token: TokenWatcher = self.parent.watchers[context.user_data['removeorder']['token_address']]
        chat_message(
            update,
            context,
            text=f'Are you sure you want to delete order #{query.data} for {token.name}?',
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton('✅ Confirm', callback_data=query.data),
                        InlineKeyboardButton('❌ Cancel', callback_data='cancel'),
                    ]
                ]
            ),
        )
        return self.next.ORDER

    @check_chat_id
    def command_removeorder_order(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        # query.answer()
        if query.data == 'cancel':
            self.cancel_command(update, context)
            return ConversationHandler.END
        assert query.data
        if not query.data.isdecimal():
            self.command_error(update, context, text='Invalid order ID')
            return ConversationHandler.END
        token: TokenWatcher = self.parent.watchers[context.user_data['removeorder']['token_address']]
        try:
            order = next(filter(lambda o: o.order_record.id == int(str(query.data)), token.orders))
        except StopIteration:
            self.command_error(update, context, text=f'Order {query.data} could not be found.')
            return ConversationHandler.END
        remove_order(order_record=order.order_record)
        token.orders.remove(order)
        chat_message(update, context, text=f'✅ Alright, the order <b>#{query.data}</b> was removed from {token.name}.')
        return ConversationHandler.END

    @check_chat_id
    def command_cancelorder(self, update: Update, context: CallbackContext):
        assert update.effective_chat and context.user_data is not None
        self.cancel_command(update, context)
        return ConversationHandler.END

    def get_type_name(self, order: OrderWatcher) -> str:
        return (
            'limit buy'
            if order.type == 'buy' and not order.above
            else 'stop loss'
            if order.type == 'sell' and not order.above
            else 'limit sell'
            if order.type == 'sell' and order.above
            else 'unknown'
        )

    def get_type_icon(self, order: OrderWatcher) -> str:
        return '🔴' if order.type == 'sell' else '🟢'

    def cancel_command(self, update: Update, context: CallbackContext):
        assert context.user_data is not None
        del context.user_data['removeorder']
        chat_message(update, context, text='⚠️ OK, I\'m cancelling this command.', edit=False)

    def command_error(self, update: Update, context: CallbackContext, text: str):
        assert context.user_data is not None
        del context.user_data['removeorder']
        chat_message(update, context, text=f'⛔️ {text}', edit=False)
