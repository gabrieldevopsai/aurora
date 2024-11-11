import os
import time
import random
from datetime import datetime, timedelta
from db.db_setup import create_database, get_db
from db.db_seed import seed_database
from pipeline import run_pipeline
from dotenv import load_dotenv
import secrets
from requests_oauthlib import OAuth1
from tweepy import Client, Paginator, TweepyException
from engines.post_sender import send_post, send_post_API
from twitter.account import Account
import json
from solders.keypair import Keypair
from solders.pubkey import Pubkey
import secrets
import logging
import base58
from db.db_setup import engine
from sqlalchemy import inspect, text

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def generate_solana_account():
    """Generate a new Solana account with private key and address."""
    keypair = Keypair()
    rawPK = bytes(keypair)  # rawPK é um objeto bytes
    private_key = base58.b58encode(rawPK)  # Codifique para Base58
    public_key = keypair.pubkey()
    solana_address = str(public_key)

    logger.info(f"Public Key: {public_key}")
    logger.info(f"Solana Address: {solana_address}")

    return private_key.decode(), solana_address

def get_random_activation_time():
    """Returns a random time within the next 10 minutes"""
    return datetime.now() + timedelta(minutes=random.uniform(0, 10))



def get_random_duration():
    """Returns a random duration between 5-10 minutes"""
    return timedelta(minutes=random.uniform(5, 10))
    #return timedelta(seconds=random.uniform(10, 10))


def get_next_run_time():
    """Returns a random time between 30 seconds and 3 minutes from now"""
    return datetime.now() + timedelta(seconds=random.uniform(30, 180))

def main():
    load_dotenv()

    # Check if the database file exists
    if not os.path.exists("./data/agents.db"):
        logger.info("Creating database...")
        create_database()
        logger.info("Seeding database...")
        seed_database()
    else:
        # upgrade_database()
        logger.info("Database already exists. Skipping creation and seeding.")

    db = next(get_db())

    # Load environment variables
    api_keys = {
        "llm_api_key": os.getenv("HYPERBOLIC_API_KEY"),
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY"),
    }

    # Accessing environment variables
    
    x_consumer_key = os.environ.get("X_CONSUMER_KEY")
    x_consumer_secret = os.environ.get("X_CONSUMER_SECRET")
    x_access_token = os.environ.get("X_ACCESS_TOKEN")
    x_access_token_secret = os.environ.get("X_ACCESS_TOKEN_SECRET")
    solana_mainnet_rpc_url = os.environ.get("SOLANA_MAINNET_RPC_URL")
    auth_tokens_raw = os.environ.get("X_AUTH_TOKENS")
    auth_tokens = json.loads(auth_tokens_raw)
    account = Account(cookies=auth_tokens)
    auth = OAuth1(x_consumer_key, x_consumer_secret, x_access_token, x_access_token_secret)

    # Generate Solana account
    # private_key_hex, solana_address = generate_solana_account()

    private_key_hex = os.environ.get("PK_WALLET")
    eth_address = os.environ.get("WALLET_ADDY")
    logger.info(f"generated agent exclusively-owned wallet: {eth_address}")
    
    # logger.info(f"Generated agent's wallet address: {solana_address}")
    # logger.info(f"Generated agent's private key: {private_key_hex}")
    # logger.info(f"Generated agent's private key: {private_key_hex}")
    # logger.info(f"Generated agent's wallet address: {solana_address}")

    # # Announce wallet address using new Account-based approach
    # tweet_id = send_post_API(auth, f'My wallet is {solana_address}')
    # logger.info(f"Wallet announcement tweet: https://x.com/user/status/{tweet_id}")
    # try:
    #     rest_id = tweet_id['data']['create_tweet']['tweet_results']['result']['rest_id']
    #     logger.info(f"Wallet announcement tweet: https://x.com/user/status/{rest_id}")
    # except KeyError:
    #     logger.info(f"Couldn't tweet wallet announcement: {tweet_id}")

    # Do initial run on start
    logger.info("\nPerforming initial pipeline run...")
    try:
        run_pipeline(
            db,
            account,
            auth,
            private_key_hex,
            solana_mainnet_rpc_url,
            **api_keys,
        )
        logger.info("Initial run completed successfully.")
    except Exception as e:
        logger.info(f"Error during initial run: {e}")

    logger.info("Starting continuous pipeline process...")

    while True:
        try:
            # Calculate next activation time and duration
            activation_time = get_random_activation_time()
            active_duration = get_random_duration()
            deactivation_time = activation_time + active_duration

            logger.info(f"\nNext cycle:")
            logger.info(f"Activation time: {activation_time.strftime('%I:%M:%S %p')}")
            logger.info(f"Deactivation time: {deactivation_time.strftime('%I:%M:%S %p')}")
            logger.info(f"Duration: {active_duration.total_seconds() / 60:.1f} minutes")

            # Wait until activation time
            while datetime.now() < activation_time:
                time.sleep(60)  # Check every minute

            # Pipeline is now active
            logger.info(f"\nPipeline activated at: {datetime.now().strftime('%H:%M:%S')}")

            # Schedule first run
            next_run = get_next_run_time()

            # Run pipeline at random intervals until deactivation time
            while datetime.now() < deactivation_time:
                if datetime.now() >= next_run:
                    logger.info(f"Running pipeline at: {datetime.now().strftime('%H:%M:%S')}")
                    try:
                        run_pipeline(
                            db,
                            account,
                            auth,
                            private_key_hex,
                            solana_mainnet_rpc_url,
                            **api_keys,
                        )
                    except Exception as e:
                        logger.info(f"Error running pipeline: {e}")

                    # Schedule next run
                    next_run = get_next_run_time()
                    logger.info(
                        f"Next run scheduled for: {next_run.strftime('%H:%M:%S')} "
                        f"({(next_run - datetime.now()).total_seconds():.1f} seconds from now)"
                    )

                # Short sleep to prevent CPU spinning
                time.sleep(1)

            logger.info(f"Pipeline deactivated at: {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            logger.info(f"Error in pipeline: {e}")
            continue


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nProcess terminated by user")
