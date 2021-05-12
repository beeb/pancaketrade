"""Bot class."""
from loguru import logger
from telegram import Update
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    ConversationHandler,
    Filters,
    MessageHandler,
    PicklePersistence,
    Updater,
)

from pancaketrade.conversations import AddTokenConversation
from pancaketrade.network.bsc import Network
from pancaketrade.persistence import db, init_db
from pancaketrade.utils.config import Config
from pancaketrade.utils.generic import check_chat_id


class TradeBot:
    """Bot class."""

    def __init__(self, config: Config):
        self.config = config
        self.net = Network(wallet=self.config.wallet, secrets=self.config.secrets)
        self.db = db
        init_db()
        persistence = PicklePersistence(filename='botpersistence')
        self.updater = Updater(token=config.secrets.telegram_token, persistence=persistence)
        self.dispatcher = self.updater.dispatcher
        self.convos = {'addtoken': AddTokenConversation()}
        self.setup_telegram()

    def setup_telegram(self):
        self.dispatcher.add_handler(CommandHandler('start', self.command_start))
        self.dispatcher.add_handler(CommandHandler('status', self.command_status))
        addtoken_handler = ConversationHandler(
            entry_points=[CommandHandler('addtoken', self.convos['addtoken'].command_addtoken)],
            states={
                self.convos['addtoken'].next.ADD_TOKEN_ADDRESS: [
                    MessageHandler(Filters.text & ~Filters.command, self.convos['addtoken'].command_addtoken_address)
                ]
            },
            fallbacks=[CommandHandler('canceltoken', self.convos['addtoken'].command_canceltoken)],
            name='addtoken_conversation',
            persistent=True,
        )
        self.dispatcher.add_handler(addtoken_handler)

    def start(self):
        self.dispatcher.bot.send_message(chat_id=self.config.secrets.admin_chat_id, text='Bot started')
        logger.info('Bot started')
        self.updater.start_polling()
        self.updater.idle()

    @check_chat_id
    def command_start(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        update.message.reply_html(
            'Hi! You can start adding tokens that you want to trade with the <a href="/addtoken">/addtoken</a> command.'
        )

    @check_chat_id
    def command_status(self, update: Update, context: CallbackContext):
        assert update.message and update.effective_chat
        balance_bnb = self.net.get_bnb_balance()
        update.message.reply_html(f'BNB in wallet: {balance_bnb:.4f}')
