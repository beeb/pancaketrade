from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    FixedCharField,
    ForeignKeyField,
    Model,
    SmallIntegerField,
    SqliteDatabase,
)

db = SqliteDatabase("user_data/pancaketrade.db")


class Token(Model):
    address = FixedCharField(max_length=42, unique=True)
    symbol = CharField()
    icon = CharField(null=True)  # emoji
    decimals = SmallIntegerField()
    default_slippage = FixedCharField(max_length=7)
    effective_buy_price = CharField(null=True)

    class Meta:
        database = db


class Order(Model):
    token = ForeignKeyField(Token, backref="orders")
    type = FixedCharField(max_length=4)  # buy (tokens for BNB) or sell (tokens for BNB)
    limit_price = CharField()  # decimal stored as string
    above = BooleanField()  # Above = True, below = False
    trailing_stop = SmallIntegerField(null=True)  # in percent
    amount = CharField()  # in wei, either BNB or token depending on "type"
    slippage = FixedCharField(max_length=7)
    # gas price in wei, if null then use network gas price.
    # If starts with "+", then we add this amount of gwei to default network price
    gas_price = CharField(null=True)
    created = DateTimeField()

    class Meta:
        database = db


class Preferences(Model):
    """Simple key-value store for checking if some preferences have changed value between restarts."""

    key = CharField(unique=True)
    value = CharField(null=True)

    class Meta:
        database = db
