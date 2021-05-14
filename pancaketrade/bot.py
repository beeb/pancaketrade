"""Bot class."""
from loguru import logger
from telegram import ParseMode, Update
from telegram.ext import CallbackContext, CommandHandler, Defaults, PicklePersistence, Updater

from pancaketrade.conversations import AddTokenConversation
from pancaketrade.network import Network
from pancaketrade.persistence import db
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import check_chat_id


class TradeBot:
    """Bot class."""

    def __init__(self, config: Config):
        self.config = config
        self.net = Network(rpc=self.config.bsc_rpc, wallet=self.config.wallet, secrets=self.config.secrets)
        self.db = db
        defaults = Defaults(parse_mode=ParseMode.HTML, disable_web_page_preview=True, timeout=120)
        persistence = PicklePersistence(filename='botpersistence')
        self.updater = Updater(token=config.secrets.telegram_token, persistence=persistence, defaults=defaults)
        self.dispatcher = self.updater.dispatcher
        self.convos = {'addtoken': AddTokenConversation(parent=self, config=self.config)}
        self.setup_telegram()

    def setup_telegram(self):
        self.dispatcher.add_handler(CommandHandler('start', self.command_start))
        self.dispatcher.add_handler(CommandHandler('status', self.command_status))
        self.dispatcher.add_handler(self.convos['addtoken'].handler)

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
        update.message.reply_html(f'BNB in wallet: {balance_bnb:.4f}')
