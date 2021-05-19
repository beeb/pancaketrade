"""Bot class."""
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, Defaults, Updater
from web3 import Web3

from pancaketrade.conversations import (
    AddTokenConversation,
    BuySellConversation,
    CreateOrderConversation,
    RemoveOrderConversation,
    RemoveTokenConversation,
    SellAllConversation,
)
from pancaketrade.network import Network
from pancaketrade.persistence import db
from pancaketrade.utils.config import Config
from pancaketrade.utils.db import get_token_watchers, init_db
from pancaketrade.utils.generic import chat_message, check_chat_id, get_tokens_keyboard_layout
from pancaketrade.watchers import OrderWatcher, TokenWatcher


class TradeBot:
    """Bot class."""

    def __init__(self, config: Config):
        self.config = config
        self.db = db
        init_db()
        self.net = Network(
            rpc=self.config.bsc_rpc,
            wallet=self.config.wallet,
            min_pool_size_bnb=self.config.min_pool_size_bnb,
            secrets=self.config.secrets,
        )
        defaults = Defaults(parse_mode=ParseMode.HTML, disable_web_page_preview=True, timeout=120)
        # persistence = PicklePersistence(filename='botpersistence')
        self.updater = Updater(token=config.secrets.telegram_token, persistence=None, defaults=defaults)
        self.dispatcher = self.updater.dispatcher
        self.convos = {
            'addtoken': AddTokenConversation(parent=self, config=self.config),
            'removetoken': RemoveTokenConversation(parent=self, config=self.config),
            'createorder': CreateOrderConversation(parent=self, config=self.config),
            'removeorder': RemoveOrderConversation(parent=self, config=self.config),
            'sellall': SellAllConversation(parent=self, config=self.config),
            'buysell': BuySellConversation(parent=self, config=self.config),
        }
        self.setup_telegram()
        self.watchers: Dict[str, TokenWatcher] = get_token_watchers(
            net=self.net, dispatcher=self.dispatcher, config=self.config
        )
        self.status_scheduler = BackgroundScheduler(
            job_defaults={
                'coalesce': True,
                'max_instances': 1,
                'misfire_grace_time': 20,
            }
        )
        self.start_status_update()
        self.last_status_message_id: Optional[int] = None

    def setup_telegram(self):
        self.dispatcher.add_handler(CommandHandler('start', self.command_start))
        self.dispatcher.add_handler(CommandHandler('status', self.command_status))
        self.dispatcher.add_handler(CommandHandler('order', self.command_order))
        self.dispatcher.add_handler(CommandHandler('addorder', self.command_addorder))
        self.dispatcher.add_handler(CommandHandler('address', self.command_address))
        self.dispatcher.add_handler(
            CallbackQueryHandler(self.command_address_selected, pattern='^address:0x[a-fA-F0-9]{40}$')
        )
        for convo in self.convos.values():
            self.dispatcher.add_handler(convo.handler)
        commands = [
            ('status', 'display all tokens and their price, orders'),
            ('addorder', 'add order to one of the tokens'),
            ('order', 'display order information, pass the order ID as argument'),
            ('addtoken', 'add a token that you want to trade'),
            ('removetoken', 'remove a token that you added'),
            ('address', 'get the contract address for a token'),
        ]
        self.dispatcher.bot.set_my_commands(commands=commands)
        self.dispatcher.add_error_handler(self.error_handler)

    def start_status_update(self):
        if not self.config.update_messages:
            return
        trigger = IntervalTrigger(seconds=15)
        self.status_scheduler.add_job(self.update_status, trigger=trigger)
        self.status_scheduler.start()

    def start(self):
        try:
            self.dispatcher.bot.send_message(chat_id=self.config.secrets.admin_chat_id, text='🤖 Bot started')
        except Exception:  # chat doesn't exist yet, do nothing
            logger.info('Chat with user doesn\'t exist yet.')
        logger.info('Bot started')
        self.updater.start_polling()
        self.updater.idle()

    @check_chat_id
    def command_start(self, update: Update, context: CallbackContext):
        chat_message(
            update,
            context,
            text='Hi! You can start adding tokens that you want to trade with the '
            + '<a href="/addtoken">/addtoken</a> command.',
            edit=False,
        )

    @check_chat_id
    def command_status(self, update: Update, context: CallbackContext):
        sorted_tokens = sorted(self.watchers.values(), key=lambda token: token.symbol.lower())
        for token in sorted_tokens:
            status, buttons = self.get_token_status(token)
            reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)
            msg = chat_message(update, context, text=status, reply_markup=reply_markup, edit=False)
            if msg is not None:
                self.watchers[token.address].last_status_message_id = msg.message_id
        balance_bnb = self.net.get_bnb_balance()
        price_bnb = self.net.get_bnb_price()
        stat_msg = chat_message(
            update, context, text=f'<b>Wallet</b>: {balance_bnb:.4f} BNB (${balance_bnb * price_bnb:.2f})', edit=False
        )
        if stat_msg is not None:
            self.last_status_message_id = stat_msg.message_id

    @check_chat_id
    def command_addorder(self, update: Update, context: CallbackContext):
        buttons_layout = get_tokens_keyboard_layout(self.watchers, callback_prefix='create_order')
        reply_markup = InlineKeyboardMarkup(buttons_layout)
        chat_message(
            update,
            context,
            text='Add order to which token?',
            reply_markup=reply_markup,
            edit=False,
        )

    @check_chat_id
    def command_order(self, update: Update, context: CallbackContext):
        error_msg = 'You need to provide the order ID number as argument to this command.'
        if context.args is None:
            chat_message(update, context, text=error_msg, edit=False)
            return
        try:
            order_id = int(context.args[0])
        except Exception:
            chat_message(update, context, text=error_msg, edit=False)
            return
        order: Optional[OrderWatcher] = None
        for token in self.watchers.values():
            for o in token.orders:
                if o.order_record.id != order_id:
                    continue
                order = o
        if not order:
            chat_message(update, context, text='⛔️ Could not find order with this ID.', edit=False)
            return
        chat_message(update, context, text=order.long_repr(), edit=False)

    @check_chat_id
    def command_address(self, update: Update, context: CallbackContext):
        buttons_layout = get_tokens_keyboard_layout(self.watchers, callback_prefix='address')
        reply_markup = InlineKeyboardMarkup(buttons_layout)
        chat_message(
            update,
            context,
            text='Get address for which token?',
            reply_markup=reply_markup,
            edit=False,
        )

    @check_chat_id
    def command_address_selected(self, update: Update, context: CallbackContext):
        assert update.callback_query
        query = update.callback_query
        assert query.data
        token_address = query.data.split(':')[1]
        if not Web3.isChecksumAddress(token_address):
            chat_message(update, context, text='⛔️ Invalid token address.', edit=self.config.update_messages)
            return
        token = self.watchers[token_address]
        chat_message(
            update, context, text=f'{token.name}\n<code>{token_address}</code>', edit=self.config.update_messages
        )

    def update_status(self):
        if self.last_status_message_id is None:
            return  # we probably did not call status since start
        balance_bnb = self.net.get_bnb_balance()
        price_bnb = self.net.get_bnb_price()
        self.dispatcher.bot.edit_message_text(
            f'<b>Wallet</b>: {balance_bnb:.4f} BNB (${balance_bnb * price_bnb:.2f})',
            chat_id=self.config.secrets.admin_chat_id,
            message_id=self.last_status_message_id,
        )
        sorted_tokens = sorted(self.watchers.values(), key=lambda token: token.symbol.lower())
        for token in sorted_tokens:
            if token.last_status_message_id is None:
                continue
            status, buttons = self.get_token_status(token)
            reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)
            self.dispatcher.bot.edit_message_text(
                status,
                chat_id=self.config.secrets.admin_chat_id,
                message_id=token.last_status_message_id,
                reply_markup=reply_markup,
            )

    def get_token_status(self, token: TokenWatcher) -> Tuple[str, List[List[InlineKeyboardButton]]]:
        buttons = [
            [
                InlineKeyboardButton('➕ Create order...', callback_data=f'create_order:{token.address}'),
            ],
            [
                InlineKeyboardButton('❗️ Sell all now!', callback_data=f'sell_all:{token.address}'),
                InlineKeyboardButton('💰 Buy/Sell now...', callback_data=f'buy_sell:{token.address}'),
            ],
        ]
        if len(token.orders):
            buttons[0].insert(
                0,
                InlineKeyboardButton('➖ Delete order...', callback_data=f'delete_order:{token.address}'),
            )
        token_balance = self.net.get_token_balance(token_address=token.address)
        token_balance_bnb = self.net.get_token_balance_bnb(token_address=token.address, balance=token_balance)
        token_balance_usd = self.net.get_token_balance_usd(token_address=token.address, balance=token_balance)
        token_price, _ = self.net.get_token_price(token_address=token.address, token_decimals=token.decimals, sell=True)
        token_price_usd = self.net.get_token_price_usd(
            token_address=token.address, token_decimals=token.decimals, sell=True
        )
        orders_sorted = sorted(
            token.orders, key=lambda o: o.limit_price if o.limit_price else Decimal(1e12), reverse=True
        )  # if no limit price (market price) display first (big artificial value)
        orders = [str(order) for order in orders_sorted]
        message = (
            f'<b>{token.name}</b>: {token_balance:,.1f}        '
            + f'<a href="https://poocoin.app/tokens/{token.address}">Chart</a>\n'
            + f'<b>Value</b>: <code>{token_balance_bnb:.3g}</code> BNB (${token_balance_usd:.2f})\n'
            + f'<b>Price</b>: <code>{token_price:.3g}</code> BNB per token (${token_price_usd:.3g})\n'
            + '<b>Orders</b>: (underlined = tracking trailing stop loss)\n'
            + '\n'.join(orders)
        )
        return message, buttons

    def error_handler(self, update: Update, context: CallbackContext) -> None:
        logger.error('Exception while handling an update')
        logger.error(context.error)
        chat_message(update, context, text=f'⛔️ Exception while handling an update\n{context.error}', edit=False)
