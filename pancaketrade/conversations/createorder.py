from typing import NamedTuple

from pancaketrade.network import Network
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import check_chat_id
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    Filters,
    MessageHandler,
)


class CreateOrderResponses(NamedTuple):
    TYPE: int = 0
    TRAILING: int = 1
    AMOUNT: int = 2
    SLIPPAGE: int = 3
    GAS: int = 4


class CreateOrderConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = CreateOrderResponses()
        self.handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.command_createorder, pattern='^create_order:0x[a-fA-F0-9]{40}$')],
            states={
                self.next.TYPE: [CallbackQueryHandler(self.command_createorder_type)],
                self.next.TRAILING: [
                    CallbackQueryHandler(self.command_createorder_trailing),
                    MessageHandler(Filters.text & ~Filters.command, self.command_createorder_trailing_custom),
                ],
            },
            fallbacks=[CommandHandler('cancelorder', self.command_cancelorder)],
            name='createorder_conversation',
            persistent=True,
            conversation_timeout=120,
        )

    @check_chat_id
    def command_createorder(self, update: Update, context: CallbackContext):
        assert update.callback_query and update.effective_chat and context.user_data is not None
        query = update.callback_query
        query.answer()
        assert query.data
        token_address = query.data.split(':')[1]
        token = self.parent.watchers[token_address]
        context.user_data['createorder'] = {'token_address': token_address}
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton('Stop loss sell', callback_data='stop_loss'),
                    InlineKeyboardButton('Take profit sell', callback_data='limit_sell'),
                ],
                [
                    InlineKeyboardButton('Limit buy', callback_data='limit_buy'),
                    InlineKeyboardButton('❌ Cancel', callback_data='cancel'),
                ],
            ]
        )
        context.dispatcher.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f'Creating order for token {token.name}.\nWhich type of order would you like to create?',
            reply_markup=reply_markup,
        )
        return self.next.TYPE

    @check_chat_id
    def command_createorder_type(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        query.answer()
        if query.data == 'cancel':
            del context.user_data['createorder']
            query.edit_message_reply_markup(reply_markup=None)
            query.edit_message_text('⚠️ OK, I\'m cancelling this command.')
            return ConversationHandler.END
        order = context.user_data['createorder']
        if query.data == 'stop_loss':
            order['type'] = 'sell'
            order['above'] = False  # below
            order['trailing_stop'] = None
            # we don't use trailing stop loss here
            query.edit_message_reply_markup(reply_markup=None)
            query.edit_message_text('OK, the order will sell when price is below target price.')
            return self.next.AMOUNT
        elif query.data == 'limit_sell':
            order['type'] = 'sell'
            order['above'] = True  # above
        elif query.data == 'limit_buy':
            order['type'] = 'buy'
            order['above'] = False  # below
        else:
            del context.user_data['createorder']
            query.edit_message_reply_markup(reply_markup=None)
            query.edit_message_text('⛔ That type of order is not supported.')
            return ConversationHandler.END
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton('1%', callback_data='1'),
                    InlineKeyboardButton('2%', callback_data='2'),
                    InlineKeyboardButton('5%', callback_data='5'),
                    InlineKeyboardButton('10%', callback_data='10'),
                ],
                [
                    InlineKeyboardButton('No trailing stop loss', callback_data='None'),
                    InlineKeyboardButton('❌ Cancel', callback_data='cancel'),
                ],
            ]
        )
        query.edit_message_reply_markup(reply_markup=reply_markup)
        query.edit_message_text(
            f'OK, the order will {order["type"]} when price is '
            + f'{"above" if order["above"] else "below"} target price.\n'
            + 'Do you want to enable trailing stop loss? If yes, what is the callback rate?\n'
            + 'You can also message me a custom value in percent.'
        )
        return self.next.TRAILING

    @check_chat_id
    def command_createorder_trailing(self, update: Update, context: CallbackContext):
        assert update.callback_query and context.user_data is not None
        query = update.callback_query
        query.answer()
        assert query.data
        query.edit_message_reply_markup(reply_markup=None)
        if query.data == 'cancel':
            del context.user_data['createorder']
            query.edit_message_reply_markup(reply_markup=None)
            query.edit_message_text('⚠️ OK, I\'m cancelling this command.')
            return ConversationHandler.END
        order = context.user_data['createorder']
        token = self.parent.watchers[order['token_address']]
        unit = 'BNB' if order['type'] == 'buy' else token.symbol
        if query.data == 'None':
            order['trailing_stop'] = None
            query.edit_message_text(
                'OK, the order will use no trailing stop loss.\n'
                + f'Next, please indicate the value in <b>{unit}</b> you would like to {order["type"]}.\n'
                + 'You can use scientific notation like <code>1.3E-4</code> if you want.'
            )
            return self.next.AMOUNT

        try:
            callback_rate = int(query.data)
        except ValueError:
            del context.user_data['createorder']
            query.edit_message_reply_markup(reply_markup=None)
            query.edit_message_text('⛔ The callback rate is not recognized.')
            return ConversationHandler.END
        order['trailing_stop'] = callback_rate
        query.edit_message_text(
            f'OK, the order will use trailing stop loss with {callback_rate}% callback.\n'
            + f'Next, please indicate the value in <b>{unit}</b> you would like to {order["type"]}.\n'
            + 'You can use scientific notation like <code>1.3E-4</code> if you want.'
        )
        return self.next.AMOUNT

    @check_chat_id
    def command_createorder_trailing_custom(self, update: Update, context: CallbackContext):
        pass

    @check_chat_id
    def command_cancelorder(self, update: Update, context: CallbackContext):
        assert update.message and context.user_data is not None
        del context.user_data['createorder']
        update.message.reply_html('⚠️ OK, I\'m cancelling this command.')
        return ConversationHandler.END
