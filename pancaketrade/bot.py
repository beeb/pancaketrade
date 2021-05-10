"""Bot class."""
from pancaketrade.network.bsc import Network
from pancaketrade.persistence.models import db, init_db
from pancaketrade.utils.config import Config


class TradeBot:
    """Bot class."""

    def __init__(self, config: Config):
        self.config = config
        self.net = Network(secrets=self.config.secrets)
        self.db = db
        init_db()
