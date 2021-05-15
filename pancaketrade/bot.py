"""Bot class."""
from typing import Dict, List

from loguru import logger
from telegram import ParseMode, Update
from telegram.ext import CallbackContext, CommandHandler, Defaults, PicklePersistence, Updater

from pancaketrade.conversations import AddTokenConversation, RemoveTokenConversation
from pancaketrade.network import Network
from pancaketrade.persistence import db
from pancaketrade.utils.config import Config
from pancaketrade.utils.db import get_token_watchers, init_db
from pancaketrade.utils.generic import check_chat_id
from pancaketrade.watchers import TokenWatcher


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
        persistence = PicklePersistence(filename='botpersistence')
        self.updater = Updater(token=config.secrets.telegram_token, persistence=persistence, defaults=defaults)
        self.dispatcher = self.updater.dispatcher
        self.convos = {
            'addtoken': AddTokenConversation(parent=self, config=self.config),
            'removetoken': RemoveTokenConversation(parent=self, config=self.config),
        }
        self.setup_telegram()
        self.watchers: Dict[str, TokenWatcher] = get_token_watchers(net=self.net, interval=self.config.monitor_interval)

    def setup_telegram(self):
        self.dispatcher.add_handler(CommandHandler('start', self.command_start))
        self.dispatcher.add_handler(CommandHandler('status', self.command_status))
        for convo in self.convos.values():
            self.dispatcher.add_handler(convo.handler)
        commands = [
            ('start', 'start interaction with the bot'),
            ('status', 'display all tokens and their price, orders'),
            ('addtoken', 'add a token that you want to trade'),
            ('removetoken', 'remove a token that you added previously'),
        ]
        self.dispatcher.bot.set_my_commands(commands=commands)

    def start(self):
        self.dispatcher.bot.send_message(chat_id=self.config.secrets.admin_chat_id, text='Bot started')
        logger.info('Bot started')
        self.updater.start_polling()
        self.updater.idle()

    @check_chat_id
    def command_start(self, update: Update, _: CallbackContext):
        assert update.message and update.effective_chat
        update.message.reply_html(
            'Hi! You can start adding tokens that you want to trade with the <a href="/addtoken">/addtoken</a> command.'
        )

    @check_chat_id
    def command_status(self, update: Update, _: CallbackContext):
        assert update.message and update.effective_chat
        balance_bnb = self.net.get_bnb_balance()
        price_bnb = self.net.get_bnb_price()
        sorted_tokens = sorted(self.watchers.values(), key=lambda token: token.symbol.lower())
        token_status: List[str] = []
        for token in sorted_tokens:
            token_balance = self.net.get_token_balance(token_address=token.address)
            token_balance_bnb = self.net.get_token_balance_bnb(token_address=token.address, balance=token_balance)
            token_balance_usd = self.net.get_token_balance_usd(token_address=token.address, balance=token_balance)
            orders = [str(order) for order in token.orders]
            token_status.append(
                f'<b>{token.name}</b>: {token_balance:,.1f}\n'
                + f'<b>Value</b>: {token_balance_bnb:.4f} (${token_balance_usd:.2f})\n'
                + '<b>Orders</b>:\n'
                + '\n'.join(orders)
            )
        update.message.reply_html(
            '<u>STATUS</u>\n' + f'<b>Wallet</b>: {balance_bnb:.4f} BNB (${balance_bnb * price_bnb:.2f})\n\n'
        )
        for status in token_status:
            update.message.reply_html(status)
