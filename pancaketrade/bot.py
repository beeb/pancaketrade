"""Bot class."""
from pancaketrade.utils.config import Config
from pancaketrade.persistence.models import init_db, db


class TradeBot:
    """Bot class."""

    def __init__(self, config: Config):
        self.config = config
        self.db = db
        init_db()
