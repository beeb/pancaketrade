"""Bot class."""
from typing import Dict, List, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, Update
from telegram.ext import CallbackContext, CommandHandler, Defaults, Updater

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
from pancaketrade.utils.generic import chat_message, check_chat_id
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
        for convo in self.convos.values():
            self.dispatcher.add_handler(convo.handler)
        commands = [
            ('status', 'display all tokens and their price, orders'),
            ('addtoken', 'add a token that you want to trade'),
            ('removetoken', 'remove a token that you added previously'),
            ('cancelorder', 'cancel the current order creation/removal process (if bot is stuck for instance)'),
            ('order', 'display detailled order information. Pass the order ID as argument.'),
        ]
        self.dispatcher.bot.set_my_commands(commands=commands)
        self.dispatcher.add_error_handler(self.error_handler)

    def start_status_update(self):
        trigger = IntervalTrigger(seconds=30)
        self.status_scheduler.add_job(self.update_status, trigger=trigger)
        # self.status_scheduler.start()

    def start(self):
        self.dispatcher.bot.send_message(chat_id=self.config.secrets.admin_chat_id, text='🤖 Bot started')
        logger.info('Bot started')
        self.updater.start_polling()
        self.updater.idle()

    @check_chat_id
    def command_start(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        chat_message(
            update,
            context,
            text='Hi! You can start adding tokens that you want to trade with the '
            + '<a href="/addtoken">/addtoken</a> command.',
        )

    @check_chat_id
    def command_status(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        balance_bnb = self.net.get_bnb_balance()
        price_bnb = self.net.get_bnb_price()
        stat_msg = context.bot.send_message(
            chat_id=update.effective_chat.id,
            text='<u>STATUS</u>\n' + f'<b>Wallet</b>: {balance_bnb:.4f} BNB (${balance_bnb * price_bnb:.2f})',
        )
        self.last_status_message_id = stat_msg.message_id
        sorted_tokens = sorted(self.watchers.values(), key=lambda token: token.symbol.lower())
        for token in sorted_tokens:
            status, buttons = self.get_token_status(token)
            reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)
            msg = context.bot.send_message(chat_id=update.effective_chat.id, text=status, reply_markup=reply_markup)
            self.watchers[token.address].last_status_message_id = msg.message_id

    @check_chat_id
    def command_order(self, update: Update, context: CallbackContext):
        assert update.message
        error_msg = 'You need to provide the order ID number as argument to this command.'
        if context.args is None:
            chat_message(update, context, text=error_msg)
            return
        try:
            order_id = int(context.args[0])
        except Exception:
            chat_message(update, context, text=error_msg)
            return
        order: Optional[OrderWatcher] = None
        for token in self.watchers.values():
            for o in token.orders:
                if o.order_record.id != order_id:
                    continue
                order = o
        if not order:
            chat_message(update, context, text='⛔️ Could not find order with this ID.')
            return
        chat_message(update, context, text=order.long_repr())

    def update_status(self):
        balance_bnb = self.net.get_bnb_balance()
        price_bnb = self.net.get_bnb_price()
        self.dispatcher.bot.edit_message_text(
            '<u>STATUS</u>\n' + f'<b>Wallet</b>: {balance_bnb:.4f} BNB (${balance_bnb * price_bnb:.2f})',
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
        orders = [str(order) for order in token.orders]
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
