# PancakeTrade - Limit orders and more for PancakeSwap on Binance Smart Chain

[![Docker Image CI](https://github.com/beeb/pancaketrade/actions/workflows/docker.yml/badge.svg?branch=develop)](https://github.com/beeb/pancaketrade/actions/workflows/docker.yml) [![Linting](https://github.com/beeb/pancaketrade/actions/workflows/lint.yml/badge.svg?branch=develop)](https://github.com/beeb/pancaketrade/actions/workflows/lint.yml)

PancakeTrade helps you create limit orders and more for your BEP-20 tokens that swap against BNB, BUSD and USDT on
PancakeSwap. The bot is controlled by Telegram so you can interact from anywhere.

![screenshot](screenshot.jpg)

## DISCLAIMER

This software is for educational purposes only. Do not risk money which you are afraid to lose. USE THE SOFTWARE AT
YOUR OWN RISK. THE AUTHORS AND ALL AFFILIATES ASSUME NO RESPONSIBILITY FOR YOUR TRADING RESULTS.

We strongly recommend you to have coding and Python knowledge. Do not hesitate to read the source code and understand
the mechanism of this bot.

## Features

The bot provides a lot of convenience trading features including:

- Tokens balance and price shown in status messages
- Price tracking relative to buy transaction
- Ability to make buy and sell limit orders including trailing stop loss
- Automatic smart price selection in case liquidity is available in BNB, BUSD and USDT (list could be extended in the
  future)
- Automatic approval for selling
- Assign emoji to each token to differentiate them easily
- Default slippage set on a token basis for faster order creation
- Charts links for each token

## Requirements

In order to use this bot, you will need the following:

- A server or computer that can run the script continuously (linux or macOS, as well as WSL have been tested)
- A Telegram bot token (interact with [@BotFather](https://telegram.me/BotFather))
- Your Telegram chat ID/user ID (interact with [@userinfobot](https://telegram.me/userinfobot))

If you choose to use the Docker image, you'll just need docker installed, see the [section below](#use-docker).

If you choose to run the script with Python, you'll need the following:

- Python version between 3.7.2 and 3.9.x
- [Poetry](https://github.com/python-poetry/poetry)
- Optional: `git` to clone this repository, you can also download the source from the github website.

## Quick Start

Before you start, make sure you have your Telegram Bot token + user ID available.
Initiate the chat with your bot on Telegram (click the "Start" button) to initialize the chat ID before you start the
bot the first time.

Run the following commands

```bash
git clone https://github.com/beeb/pancaketrade.git
cd pancaketrade
poetry install --no-dev
cp user_data/config.example.yml user_data/config.yml
```

Next, open the `config.yml` file inside the `user_data` folder with a text editor and populate the `secrets` section.

The bot needs your wallet's private key in order to sign and execute orders on your behalf. You have multiple options
to do so:

- You can run the `poetry run trade` command as-is and enter your private key in the prompt that will be shown each time
  the bot is started (only stored in memory)
- You can provide an environment variable named `WALLET_PK`. For this multiple options:
  - You can create a `.env` file in the root of the project (same folder as `pyproject.toml`) and enter your private key
    in that file (which is excluded from git): `WALLET_PK=123abcd...`
  - You can prepend the command with the environment variable (not recommended as this will be stored in your shell
    history): `WALLET_PK=123abcd... poetry run trade`
  - You can use a service to run the bot, see the [relevant section below](#run-as-a-service)

The private key is **not** the same as the seed words/mnemonic. You need the 64-characters hexadecimal private key.

Your wallet address will be inferred from the private key and doesn't need to be provided.

Run the bot:

```bash
poetry run trade
```

You will receive a notification in the Telegram chat after entering your private key. You can then start by adding your
tokens with the `/addtoken` chat command.

The other most useful command is the `/status` command that will display all your tokens and the existing orders.

## Configuration file

The script looks for a file named `config.yml` located inside the `user_data`folder by default.
You can pass another file path to the `trade` command as a positional argument.

The only parameter that is not self-explanatory is `min_pool_size_bnb`. Some tokens have multiple liquidity pools with
different pairs, like BNB and BUSD.
However, sometimes the LP with a given base token has very little liquidity, which means that the price is very volatile
and price impact is large.
In order to avoid swapping on the pair that has little liquidity, the bot checks that at least `min_pool_size_bnb`
is staked in the LP. If that's not the case, the bot will use another LP when possible.

The `update_messages` parameter will update the status messages every 30 seconds if set to `true`.
If you have trouble with the inline buttons not working, this means this bot token is not able to update messages anymore.
It's unclear what the reason is, but it happened a few times to the developer and testers of this bot.
The solution is to create a new bot token and try again, or disable `update_messages` (not ideal).

```yaml
---
bsc_rpc: 'https://bsc-dataseed.binance.org:443' # you can use any BSC RPC url you want
min_pool_size_bnb: 25 # PancakeSwap LPs that have less than 25 BNB will not be considered
max_price_impact: 0.05 # if price impact is above 5%, order will not execute
monitor_interval: 5 # the script will check the token prices with this interval in seconds
update_messages: true # status messages will update periodically to show current values
price_in_usd: true # input, show and track prices in USD/token instead of BNB/token
charts: # these are all the chart links that can be included in status messages. Remove the ones you don't need
  - poocoin
  - bogged
  - dexguru
  - dextools
  - dexscreener
secrets:
  telegram_token: 'enter_your:bot_token' # enter your Telegram Bot token
  admin_chat_id: 123456 # enter your chat ID/user ID to prevent other users to use the bot
```

## Updating the bot

When a new version is released, if you cloned the repository with git, you can simply perform a `git pull` on the master
branch. After that, run the `poetry install --no-dev` command again to update dependencies.

## Use docker

This bot now gets published as docker images on [Docker Hub](https://hub.docker.com/repository/docker/vbersier/pancaketrade).

Before you start, make sure you have your Telegram Bot token + user ID available.
Initiate the chat with your bot on Telegram (click the "Start" button) to initialize the chat ID before you start the
bot the first time.

Steps to use docker:

1. Copy the example `docker-compose.example.yml` file in this repository, rename it to `docker-compose.yml`
2. Create a file named `.env` next to your docker-compose file and insert your private key: `WALLET_PK=123abc...`
3. Create a `user_data` folder if it doesn't already exist, and create your `config.yml` file inside
   (see [previous section](#configuration-file)).
4. Run the service with `docker-compose up -d`.

Note: the bot will create a file for the database named `pancaketrade.db` inside the `user_data` folder on your local machine.
Do not delete or move that file because it holds all your token configurations and orders data. This is the file to back
up if you want to move the bot elsewhere, etc.

## Run as a service

On systems that support `systemd`, you can use the included `pancaketrade.service` file to run this script as a service.

```bash
cp pancaketrade.service ~/.config/systemd/user/
# edit the new file in .config/systemd/user with your wallet private key
systemctl --user start pancaketrade.service
systemctl --user enable pancaketrade.service # run at launch
```

## Donations

If you feel like this project has helped you and you wish to donate to the developer, you can do so on the Ethereum or
Binance Smart Chain networks at the address:

`0x026E539B566DcFF02af980d938deCcb11255d519`

Thanks for your contribution!

## Contribute

In order to contribute, make sure you install dev dependencies with `poetry install`. This repo is setup to use
pre-commit hooks, please install them with `pre-commit install` before committing.

To add your contribution, fork the repo, create a new branch off of `develop` and work in there, then create a pull
request against the `develop` branch of this repo.
