#!/usr/bin/env python
# -*- coding: utf-8 -*-

from logzero import logger
import logzero

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as ec
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

import requests
import argparse
import os, time, datetime, pytz
import imaplib, email, re, pyotp # Keep imports from mf.py for now, might remove unused later
import yfinance as yf # Import yfinance

# Import the function from the local script
# Also ensure necessary dependencies for getHistoricalCurrency are available
try:
    # Attempt to import pandas and percache to check availability early
    import pandas
    import percache
    from getHistoricalCurrency import getHistoricalCurrency
except ImportError as import_err:
    logger.error(f"Could not import dependencies or getHistoricalCurrency: {import_err}. Make sure getHistoricalCurrency.py is present and pandas/percache are installed.")
    # Exit early if import fails
    import sys
    sys.exit(1)


# --- Argument Parser ---
def parse_args():
    parser = argparse.ArgumentParser(description='MoneyForwardの資産履歴を更新します')    # 日本語説明に変更
    parser.add_argument('--start-date', help='開始日 (YYYY-MM-DD形式)')                 # 任意パラメータに変更
    parser.add_argument('--end-date', help='終了日 (YYYY-MM-DD形式)')                   # 任意パラメータに変更
    parser.add_argument('--last-month', action='store_true',                          # 先月分処理オプション追加
                      help='先月1ヶ月分を処理します')
    args = parser.parse_args()

    # 先月分の処理が指定された場合
    if args.last_month:
        # 今日の日付から先月の日付範囲を計算
        today = datetime.date.today()
        # 今月1日を取得
        first_day_of_this_month = today.replace(day=1)
        # 先月末日 = 今月1日 - 1日
        end_dt = first_day_of_this_month - datetime.timedelta(days=1)
        # 先月1日 = 先月末日の日付を1に変更
        start_dt = end_dt.replace(day=1)
        logger.info(f"🗓️ 先月分を処理します：{start_dt} から {end_dt} まで")
    else:
        # 日付指定がない場合はエラー
        if not args.start_date or not args.end_date:
            parser.error("--last-month オプションか、--start-date と --end-date の両方を指定してください")
        
        try:
            start_dt = datetime.datetime.strptime(args.start_date, '%Y-%m-%d').date()
            end_dt = datetime.datetime.strptime(args.end_date, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError("日付の形式が不正です。YYYY-MM-DD形式で指定してください")

        if start_dt > end_dt:
            raise ValueError("開始日が終了日より後の日付になっています")

    return args, start_dt, end_dt


class MoneyForwardEditor:
    # Remove date dependency from init
    def __init__(self) -> None:
        self.stock_price_cache: dict[str, float] = dict()
        self.exchange_rate_cache: dict[str, float] = dict() # Cache for exchange rates
        self.last_calculated_values: dict[str, int] = dict() # Store last known value per asset
        # Ensure necessary environment variables are set early
        if not "ALPHAVANTAGE_API_KEY" in os.environ:
            raise ValueError("env ALPHAVANTAGE_API_KEY is not found.")
        self.alphavantage_apikey = os.environ["ALPHAVANTAGE_API_KEY"]
        if not "MF_ID" in os.environ or not "MF_PASS" in os.environ:
            raise ValueError("env MF_ID and/or MF_PASS are not found.")
        self.mf_id = os.environ["MF_ID"]
        self.mf_pass = os.environ["MF_PASS"]
        # 株価取得ソース: "alphavantage"（デフォルト）または "yfinance"
        # 環境変数 STOCK_PRICE_SOURCE で切り替え可能
        self.stock_price_source = os.getenv("STOCK_PRICE_SOURCE", "yfinance").lower()
        logger.info(f"Stock price source: {self.stock_price_source}")
    # Keep init_selenium, login, 2FA handlers, data fetching methods as they are


    def init_selenium(self):
        logger.info("selenium initializing...")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=800x1000")
        options.add_argument("--disable-application-cache")
        options.add_argument("--disable-infobars")
        options.add_argument("--no-sandbox")
        options.add_argument("--hide-scrollbars")
        options.add_argument("--lang=ja-JP")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36")
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 30)
        self.driver.implicitly_wait(10)
    # login method remains the same

    def login(self):
        # self.driver.execute_script("window.open()") # Remove unnecessary window.open()
        if not "MF_ID" in os.environ or not "MF_PASS" in os.environ:
            raise ValueError("env MF_ID and/or MF_PASS are not found.")
        mf_id = os.environ["MF_ID"]
        mf_pass = os.environ["MF_PASS"]

        try: # ★追加: ログインページ読み込み処理をtryで囲む
            logger.debug("Navigating to sign-in page...") # ★追加: デバッグログ
            self.driver.get("https://moneyforward.com/sign_in")
            logger.debug("Sign-in page navigation initiated. Waiting for email input field...") # ★追加: デバッグログ
            # ★修正: ページ全体ではなく、メール入力フィールドが表示されるのを待つ
            email_input_xpath = '//input[@type="email"]'
            self.wait.until(ec.presence_of_element_located((By.XPATH, email_input_xpath)))
            logger.debug("Email input field is present on sign-in page.") # ★追加: デバッグログ
        except Exception as login_page_err: # ★追加: 例外を捕捉
            logger.error(f"Failed to load or find elements on sign-in page: {type(login_page_err).__name__} - {login_page_err}", exc_info=True)
            # ★追加: ログインページ読み込み失敗時にエラー情報を保存
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            error_filename_base = f"login_load_error_{timestamp}"
            try:
                self.driver.save_screenshot(f"{error_filename_base}.png")
                with open(f"{error_filename_base}.html", "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                logger.info(f"Saved screenshot and HTML source to {error_filename_base}.png/html")
            except Exception as save_err:
                logger.error(f"Failed to save error screenshot/HTML during login load failure: {save_err}")
            raise # ★追加: 例外を再送出してスクリプトを停止

        login_time = datetime.datetime.now(pytz.timezone("Asia/Tokyo"))
        self.send_to_element(email_input_xpath, mf_id) # ★修正: email_input_xpath を再利用

        # ★修正: クリック処理とパスワードフィールド待機処理を修正・統合
        submit_button_1 = self.driver.find_element(by=By.XPATH, value='//button[@id="submitto"]')
        logger.debug("Clicking first submit button (after email)...") # ★追加: ログ
        submit_button_1.click()
        logger.debug("First submit button clicked. Waiting for password field...") # ★追加: ログ

        password_field_xpath = '//input[@type="password"]' # Use simple XPath
        try:
            # ★修正: time.sleep(0.5) を削除し、パスワード入力欄がクリック可能になるまで明示的に待つ
            self.wait.until(ec.element_to_be_clickable((By.XPATH, password_field_xpath)))
            logger.debug("Password field is clickable.") # ★追加: ログ
        except TimeoutException:
            logger.error("Password field did not become clickable after submitting email.", exc_info=True)


        # パスワード入力と2回目の送信
        self.send_to_element(password_field_xpath, mf_pass)
        logger.debug("Clicking second submit button (after password)...") # ★追加: ログ
        self.driver.find_element(by=By.XPATH, value='//button[@id="submitto"]').click()
        logger.debug("Second submit button clicked. Waiting for page load...") # ★追加: ログ
        self.wait.until(ec.presence_of_all_elements_located)
        logger.debug("Page loaded after second submit.") # ★追加: ログ
        """
        try:
            # ★追加: エラー情報を保存
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            error_filename_base = f"password_field_error_{timestamp}"
            self.driver.save_screenshot(f"{error_filename_base}.png")
            with open(f"{error_filename_base}.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logger.info(f"Saved screenshot and HTML source to {error_filename_base}.png/html")
        except Exception as save_err:
            logger.error(f"Failed to save error screenshot/HTML during password field wait failure: {save_err}")
        """

        if self.driver.find_elements(by=By.ID, value="page-home"):
            logger.info("successfully logged in.")
        # New type of MoneyForward two step verifications
        elif self.driver.current_url.startswith("https://id.moneyforward.com/two_factor_auth/totp"):
            self.confirm_two_step_verification_param()
            if os.environ["MF_TWO_STEP_VERIFICATION"].lower() == "totp":
                confirmation_code = self.get_confirmation_code_from_totp()
            else:
                raise ValueError("unsupported two step verification is found. check your env MF_TWO_STEP_VERIFICATION.")
            self.send_to_element('//*[@name="otp_attempt"]', confirmation_code)
            self.driver.find_element(by=By.XPATH, value='//button[@id="submitto"]').click()
            self.wait.until(ec.presence_of_all_elements_located)
            if self.driver.find_elements(by=By.XPATH, value='//div[contains(@class,"registerLaterWrapper")]/a'):
                logger.info("recognized as unknown devise and selecting register later.")
                self.driver.find_element(
                    by=By.XPATH,
                    value='//div[contains(@class,"registerLaterWrapper")]/a',
                ).click()
                self.wait.until(ec.presence_of_all_elements_located)
            if self.driver.find_elements(by=By.ID, value="home"):
                logger.info("successfully logged in.")
            else:
                logger.debug(self.driver.current_url)
                raise ValueError("failed to log in.")
        # Old type of MoneyForward two step verifications
        elif self.driver.find_elements(by=By.ID, value="page-two-step-verifications"):
            self.confirm_two_step_verification_param()
            if os.environ["MF_TWO_STEP_VERIFICATION"].lower() == "gmail":
                logger.info("waiting confirmation code from Gmail...")
                confirmation_code = self.get_confirmation_code_from_gmail(login_time)
            else:
                raise ValueError("unsupported two step verification is found. check your env MF_TWO_STEP_VERIFICATION.")
            self.driver.get("https://moneyforward.com/users/two_step_verifications/verify/{confirmation_code}".format(confirmation_code=confirmation_code))
            self.wait.until(ec.presence_of_all_elements_located)
            self.driver.get("https://moneyforward.com/users/sign_in")
            if self.driver.find_elements(by=By.ID, value="home"):
                logger.info("successfully logged in.")
            else:
                raise ValueError("failed to log in.")
        else:
            raise ValueError("failed to log in.")

    ################## Two step verification ###################

    def confirm_two_step_verification_param(self):
        logger.info("two step verification is enabled.")
        if not "MF_TWO_STEP_VERIFICATION" in os.environ:
            raise ValueError("env MF_TWO_STEP_VERIFICATION is not found.")

    def get_confirmation_code_from_totp(self):
        secret_key = os.getenv("MF_TWO_STEP_VERIFICATION_TOTP_SECRET_KEY")
        if not secret_key:
            raise ValueError("env MF_TWO_STEP_VERIFICATION_TOTP_SECRET_KEY is not found.")
        # 空白を取り除く (secret_key is now guaranteed str)
        secret_key = secret_key.replace(" ", "")
        # 改行を取り除く
        secret_key = secret_key.replace("\n", "")
        # タブを取り除く
        secret_key = secret_key.replace("\t", "")
        # 改行を取り除く
        secret_key = secret_key.replace("\r", "")
        confirmation_code = pyotp.TOTP(secret_key).now()
        return confirmation_code

    def get_confirmation_code_from_gmail(self, sent_since):
        gmail_account = os.getenv("MF_TWO_STEP_VERIFICATION_GMAIL_ACCOUNT")
        gmail_app_pass = os.getenv("MF_TWO_STEP_VERIFICATION_GMAIL_APP_PASS")
        if not gmail_account or not gmail_app_pass:
            raise ValueError("env MF_TWO_STEP_VERIFICATION_GMAIL_ACCOUNT and/or MF_TWO_STEP_VERIFICATION_GMAIL_APP_PASS are not found.")
        timeout = int(os.getenv("MF_TWO_STEP_VERIFICATION_TIMEOUT", "180"))
        interval = int(os.getenv("MF_TWO_STEP_VERIFICATION_INTERVAL", "5"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            confirmation_code = self.read_confirmation_code_from_gmail(sent_since)
            if confirmation_code:
                return confirmation_code
            time.sleep(interval)

    def read_confirmation_code_from_gmail(self, sent_since):
        gmail_account = os.getenv("MF_TWO_STEP_VERIFICATION_GMAIL_ACCOUNT")
        gmail_app_pass = os.getenv("MF_TWO_STEP_VERIFICATION_GMAIL_APP_PASS")
        # Add checks for None before using
        if not gmail_account or not gmail_app_pass:
             raise ValueError("Gmail account or app password environment variables not set.")
        gmail = imaplib.IMAP4_SSL("imap.gmail.com", 993) # Correct port type to int
        gmail.login(gmail_account, gmail_app_pass) # Now guaranteed to be str
        gmail.select()
        search_option = '(FROM "feedback@moneyforward.com" SENTSINCE {sent_since})'.format(sent_since=sent_since.strftime("%d-%b-%Y"))
        head, data = gmail.search(None, search_option)

        confirmation_code = ""
        # Ensure data is not empty before splitting
        if data and data[0]:
            for num in data[0].split():
                h, d = gmail.fetch(num, "(RFC822)")
                # Check if fetch was successful and returned data
                if not d or not isinstance(d[0], tuple) or len(d[0]) < 2:
                    logger.warning(f"Could not fetch email data for message number {num}")
                    continue
                raw_email = d[0][1]
                # Check if raw_email is bytes
                if not isinstance(raw_email, bytes):
                    logger.warning(f"Expected bytes for raw email data, got {type(raw_email)} for message number {num}")
                    continue
                try:
                    message = email.message_from_bytes(raw_email) # Use message_from_bytes
                except Exception as e:
                    logger.warning(f"Could not parse email message {num}: {e}")
                    continue

                # Safer subject decoding
                subject = ""
                subject_header_list = email.header.decode_header(message.get("Subject", "(No Subject)"))
                message_encoding = None # Track encoding from subject if possible
                for decoded_part, encoding in subject_header_list:
                    if encoding: message_encoding = encoding # Store last known encoding
                    if isinstance(decoded_part, bytes):
                        try:
                            subject += decoded_part.decode(encoding or 'iso-2022-jp', errors='replace')
                        except LookupError: # Handle unknown encoding
                            subject += decoded_part.decode('iso-2022-jp', errors='replace')
                    elif isinstance(decoded_part, str):
                        subject += decoded_part
                subject = subject.strip() # Clean up whitespace

                if subject != "【マネーフォワード ME】2段階認証メール":
                    continue

                # Safer date parsing
                message_time = None
                date_str = message.get("Date")
                if date_str:
                    try:
                        # Use parsedate_to_datetime for robustness
                        message_time = email.utils.parsedate_to_datetime(date_str)
                    except Exception as e:
                        logger.warning(f"Could not parse date string '{date_str}': {e}")

                if message_time and sent_since < message_time:
                    # Robust body extraction
                    body = ""
                    if message.is_multipart():
                        for part in message.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))
                            try:
                                # Look for plain text parts that are not attachments
                                if content_type == "text/plain" and "attachment" not in content_disposition:
                                    part_payload = part.get_payload(decode=True)
                                    charset = part.get_content_charset() or message_encoding or 'iso-2022-jp' # Use detected encoding
                                    body = part_payload.decode(charset, errors='replace')
                                    break # Found plain text body
                            except Exception as e:
                                logger.warning(f"Error decoding email part: {e}")
                    else: # Not multipart, get payload directly
                        try:
                            part_payload = message.get_payload(decode=True)
                            charset = message.get_content_charset() or message_encoding or 'iso-2022-jp' # Use detected encoding
                            body = part_payload.decode(charset, errors='replace')
                        except Exception as e:
                            logger.warning(f"Error decoding single part email body: {e}")

                    if body: # Check if body was successfully extracted
                        m = re.search(
                            r"https://moneyforward.com/users/two_step_verifications/verify/([0-9]+)",
                            body,
                        )
                        if m: # Check if regex matched
                            confirmation_code = m.group(1)
                            # Update sent_since only if code found and it's the latest email processed so far
                            # This assumes emails are roughly ordered by fetch, which isn't guaranteed
                            # A better approach might be to find the max time after the loop
                            sent_since = message_time
                            logger.info(f"Found confirmation code {confirmation_code} in email from {message_time}")
                            # Keep searching in case multiple emails arrived
                        else:
                            logger.debug(f"Verification URL pattern not found in email from {message_time}")
                    else:
                        logger.warning(f"Could not extract body from email received at {message_time}")

        gmail.close()
        gmail.logout()
        return confirmation_code

    ############################################################

    def get_historical_stock_price(self, ticker: str, date_str: str) -> float | None:
        """Fetches historical stock price from Alpha Vantage."""
        logger.debug(f"Fetching historical stock price for {ticker} on {date_str}")
        # Check cache first
        cache_key = f"{ticker}_{date_str}"
        if cache_key in self.stock_price_cache:
            logger.debug(f"Using cached stock price for {cache_key}")
            return self.stock_price_cache[cache_key]

        # Use TIME_SERIES_DAILY API
        # Note: Free tier might have limitations (e.g., last 100 data points)
        # Consider 'outputsize=full' for more data, but might hit API limits faster.
        url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={ticker}&apikey={self.alphavantage_apikey}&outputsize=compact"
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            data = r.json()

            if "Time Series (Daily)" not in data:
                logger.error(f"Could not find 'Time Series (Daily)' in Alpha Vantage response for {ticker}. Response: {data}")
                return None
            if date_str not in data["Time Series (Daily)"]:
                 # Check if the requested date is a weekend or holiday
                 requested_dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
                 # Try finding the closest previous business day within the last few days
                 for i in range(1, 5):
                     prev_date = requested_dt - datetime.timedelta(days=i)
                     prev_date_str = prev_date.strftime('%Y-%m-%d')
                     if prev_date_str in data["Time Series (Daily)"]:
                         logger.warning(f"Data for {ticker} not found on {date_str}. Using data from previous business day: {prev_date_str}")
                         date_str = prev_date_str
                         break
                 else: # If loop completes without break
                    logger.error(f"Could not find stock data for {ticker} on or near {date_str}. Available dates might be limited. Response keys: {list(data['Time Series (Daily)'].keys())[:5]}...")
                    return None

            # Get the closing price for the target date
            price = float(data["Time Series (Daily)"][date_str]["4. close"])
            logger.debug(f"Fetched stock price for {ticker} on {date_str}: {price}")
            self.stock_price_cache[cache_key] = price
            return price

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching stock price for {ticker} from Alpha Vantage: {e}")
            return None
        except (KeyError, ValueError) as e:
             logger.error(f"Error parsing stock price data for {ticker} on {date_str}: {e}. Response: {data}")
             return None

    # --- New implementation using yfinance ---
    def get_historical_stock_price_yf(self, ticker: str, date_str: str) -> float | None:
        """Fetches historical stock price from Yahoo Finance using yfinance."""
        logger.debug(f"Fetching historical stock price for {ticker} on {date_str} using yfinance")
        cache_key = f"{ticker}_{date_str}_yf"
        if cache_key in self.stock_price_cache:
            logger.debug(f"Using cached yfinance stock price for {cache_key}")
            return self.stock_price_cache[cache_key]

        try:
            target_dt = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
            # yfinance history needs end date to be exclusive, so add one day
            end_dt = target_dt + datetime.timedelta(days=1)
            start_dt_str = target_dt.strftime('%Y-%m-%d')
            end_dt_str = end_dt.strftime('%Y-%m-%d')

            stock = yf.Ticker(ticker)
            # Fetch data for the single day
            hist = stock.history(start=start_dt_str, end=end_dt_str)

            if hist.empty:
                # If no data for the target date (weekend/holiday), try previous days
                logger.warning(f"No yfinance data found for {ticker} on {date_str}. Trying previous days.")
                for i in range(1, 5):
                    prev_target_dt = target_dt - datetime.timedelta(days=i)
                    prev_end_dt = prev_target_dt + datetime.timedelta(days=1)
                    prev_start_str = prev_target_dt.strftime('%Y-%m-%d')
                    prev_end_str = prev_end_dt.strftime('%Y-%m-%d')
                    hist = stock.history(start=prev_start_str, end=prev_end_str)
                    if not hist.empty:
                        price = hist['Close'].iloc[0]
                        logger.warning(f"Using yfinance data for {ticker} from previous business day: {prev_start_str} -> {price}")
                        self.stock_price_cache[cache_key] = price # Cache under original date key
                        return price
                # If still no data after checking previous days
                logger.error(f"Could not find yfinance stock data for {ticker} on or near {date_str}.")
                return None
            else:
                # Data found for the target date
                price = hist['Close'].iloc[0]
                logger.debug(f"Fetched yfinance stock price for {ticker} on {date_str}: {price}")
                self.stock_price_cache[cache_key] = price
                return price

        except Exception as e:
            logger.error(f"Error fetching or parsing yfinance stock price for {ticker} on {date_str}: {e}")
            logger.exception("Traceback from yfinance call:")
            return None

    # --- Modified get_historical_exchange_rate with fallback ---
    def get_historical_exchange_rate(self, date_str_iso: str) -> float | None:
        """Fetches historical USD/JPY exchange rate using getHistoricalCurrency.py,
           falling back to the previous day(s) if the target date has no data."""
        logger.debug(f"Fetching historical USD/JPY rate for {date_str_iso} using getHistoricalCurrency")

        # Check cache first (using ISO date string as key)
        if date_str_iso in self.exchange_rate_cache:
            logger.debug(f"Using cached exchange rate for {date_str_iso}")
            return self.exchange_rate_cache[date_str_iso]

        original_date_obj = datetime.datetime.strptime(date_str_iso, '%Y-%m-%d').date()
        rate = None
        found_date_str_ufj = None

        # Try target date first, then fallback up to 5 previous days
        for i in range(6): # 0 is target date, 1-5 are previous days
            current_target_date = original_date_obj - datetime.timedelta(days=i)
            date_str_ufj = current_target_date.strftime('%y/%m/%d') # Use YY/MM/DD

            if i > 0:
                logger.warning(f"Rate not found for {original_date_obj.strftime('%Y-%m-%d')}, trying previous day: {current_target_date.strftime('%Y-%m-%d')} ({date_str_ufj})")

            try:
                result_tuple = getHistoricalCurrency('US Dollar', date_str_ufj)

                if result_tuple is None or len(result_tuple) < 4:
                    logger.error(f"getHistoricalCurrency returned an unexpected result for {date_str_ufj}: {result_tuple}")
                    continue # Try previous day

                currency_name, tts, ttm, ttb = result_tuple

                if isinstance(ttm, str) and ttm == '<NA>':
                    logger.debug(f"No rate available for {date_str_ufj} (returned '<NA>').")
                    continue # Try previous day
                else:
                    try:
                        rate = float(ttm)
                        found_date_str_ufj = date_str_ufj
                        if i > 0:
                             logger.warning(f"Using rate {rate} from {current_target_date.strftime('%Y-%m-%d')} for original date {date_str_iso}")
                        else:
                             logger.debug(f"Fetched USD/JPY TTM rate via getHistoricalCurrency for {date_str_ufj}: {rate}")
                        break # Found a valid rate, exit loop
                    except (ValueError, TypeError) as conv_err:
                        logger.error(f"Could not convert TTM value '{ttm}' to float for {date_str_ufj}: {conv_err}")
                        continue # Try previous day (though this indicates an issue)

            except Exception as e:
                logger.error(f"Error calling getHistoricalCurrency for {date_str_ufj}: {e}")
                logger.exception("Traceback from getHistoricalCurrency call:")
                # Don't immediately fail, try previous day unless it's the last attempt
                if i == 5:
                    return None # Failed after all attempts
                continue # Try previous day

        # After the loop
        if rate is not None:
            self.exchange_rate_cache[date_str_iso] = rate # Cache under the *original* requested date
            return rate
        else:
            # Use i here, which will be 5 if the loop finished without break
            logger.error(f"Could not retrieve valid TTM exchange rate for US Dollar on or before {date_str_iso} after checking {i+1} days.")
            return None


    def get_tickers_from_page(self, date: datetime.date) -> list[str]:
        """指定日の履歴ページから #形式の資産のティッカーを収集して返す。"""
        date_str = date.strftime('%Y-%m-%d')
        self.driver.get(f"https://moneyforward.com/bs/history/list/{date_str}")
        tickers = []
        try:
            rows_xpath = "//tr[td[1][starts-with(normalize-space(.), '#')]]"
            WebDriverWait(self.driver, 60).until(ec.presence_of_element_located((By.XPATH, rows_xpath)))
            for row in self.driver.find_elements(By.XPATH, rows_xpath):
                try:
                    asset_name = row.find_element(By.XPATH, "./td[1]").text
                    if asset_name.startswith("#"):
                        parts = asset_name.split("-")
                        if len(parts) == 3:
                            tickers.append(parts[1])
                except Exception:
                    pass
        except TimeoutException:
            logger.warning(f"Could not load history page for {date_str} to collect tickers.")
        return list(set(tickers))

    def prefetch_stock_prices(self, tickers: list[str], start_date: datetime.date, end_date: datetime.date):
        """全銘柄・全期間の株価を一括取得してキャッシュに保存する。
        STOCK_PRICE_SOURCE に応じてソースを選択。yfinance使用時は失敗時にAlpha Vantageへフォールバック。"""
        if self.stock_price_source == "yfinance":
            self._prefetch_stock_prices_yfinance(tickers, start_date, end_date)
        else:
            self._prefetch_stock_prices_alphavantage(tickers, start_date, end_date)

    def _prefetch_stock_prices_yfinance(self, tickers: list[str], start_date: datetime.date, end_date: datetime.date):
        """yfinanceで全期間の株価を一括取得してキャッシュに保存する。失敗時はAlpha Vantageにフォールバック。"""
        start_str = start_date.strftime('%Y-%m-%d')
        fetch_end_str = (end_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        fallback_tickers = []
        for ticker in tickers:
            logger.info(f"Prefetching {ticker} from {start_str} to {end_date.strftime('%Y-%m-%d')} via yfinance...")
            try:
                hist = yf.Ticker(ticker).history(start=start_str, end=fetch_end_str)
                if hist.empty:
                    logger.warning(f"No yfinance data for {ticker}. Falling back to Alpha Vantage.")
                    fallback_tickers.append(ticker)
                    continue
                for ts, row in hist.iterrows():
                    cache_key = f"{ticker}_{ts.strftime('%Y-%m-%d')}_yf"
                    self.stock_price_cache[cache_key] = float(row['Close'])
                logger.info(f"Cached {len(hist)} days of prices for {ticker} via yfinance.")
            except Exception as e:
                logger.warning(f"yfinance failed for {ticker}: {e}. Falling back to Alpha Vantage.")
                fallback_tickers.append(ticker)
        if fallback_tickers:
            self._prefetch_stock_prices_alphavantage(fallback_tickers, start_date, end_date)

    def _prefetch_stock_prices_alphavantage(self, tickers: list[str], start_date: datetime.date, end_date: datetime.date):
        """Alpha Vantage TIME_SERIES_DAILY(outputsize=full)で全期間の株価を一括取得してキャッシュに保存する。"""
        for ticker in tickers:
            logger.info(f"Prefetching {ticker} via Alpha Vantage (1 API call, full history)...")
            try:
                url = (f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
                       f"&symbol={ticker}&apikey={self.alphavantage_apikey}&outputsize=compact")
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                data = r.json()
                if "Time Series (Daily)" not in data:
                    logger.error(f"Alpha Vantage: unexpected response for {ticker}: {data}")
                    continue
                count = 0
                for date_str, values in data["Time Series (Daily)"].items():
                    try:
                        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        continue
                    if start_date <= date_obj <= end_date:
                        cache_key = f"{ticker}_{date_str}_yf"
                        self.stock_price_cache[cache_key] = float(values["4. close"])
                        count += 1
                logger.info(f"Alpha Vantage: cached {count} days of prices for {ticker}.")
            except Exception as e:
                logger.error(f"Error prefetching {ticker} via Alpha Vantage: {e}")

    # Modify edit_history to accept the target date
    def edit_history(self, target_date: datetime.date):
        """Navigates to the history page for the given date and edits assets."""
        target_date_str = target_date.strftime('%Y-%m-%d')
        history_url = f"https://moneyforward.com/bs/history/list/{target_date_str}"
        logger.info(f"--- Processing Date: {target_date_str} ---")
        logger.info(f"Navigating to history page: {history_url}")
        self.driver.get(history_url)
        try:
            # Wait up to 60s for the *first cell* of a row containing an asset starting with '#'
            # This is more robust if table class/structure changes, but assumes '#' assets exist.
            first_asset_cell_xpath = "//tr/td[1][starts-with(normalize-space(.), '#')]"
            logger.info(f"Waiting up to 60 seconds for the first target asset cell ({first_asset_cell_xpath}) to load...")
            WebDriverWait(self.driver, 60).until(ec.presence_of_element_located((By.XPATH, first_asset_cell_xpath)))
            logger.info(f"History page content (first target asset cell) for {target_date_str} seems loaded.")
        except TimeoutException:
            logger.error(f"Could not find any target asset cell ('#...') on {history_url} within 60 seconds.")
            self.print_html_and_screenshot(f"history_load_error_{target_date_str}")
            return # Cannot proceed for this date if table doesn't load

        # --- Find and process editable assets ---
        # This part needs careful implementation based on the actual HTML structure of the history page.
        logger.info("Starting to process history table rows...")

        # Find rows that contain a first cell starting with '#' - more robust than assuming table class/tbody
        rows_xpath = "//tr[td[1][starts-with(normalize-space(.), '#')]]"
        logger.info(f"Looking for rows using XPath: {rows_xpath}")
        try:
            # It might take a moment for all rows to be available after the first cell is present
            # Add a short wait specifically for these rows
            WebDriverWait(self.driver, 10).until(ec.presence_of_element_located((By.XPATH, rows_xpath)))
            rows = self.driver.find_elements(By.XPATH, rows_xpath)
            logger.info(f"Found {len(rows)} potential target rows.")
        except (NoSuchElementException, TimeoutException):
            logger.error(f"Could not find any target rows using XPath: {rows_xpath}")
            return # Cannot proceed for this date if rows aren't found

        for i, row in enumerate(rows):
            asset_name = "" # Initialize asset_name for error logging
            try:
                # Find asset name in the first column (td[1])
                asset_name_element = row.find_element(By.XPATH, "./td[1]")
                asset_name = asset_name_element.text
                logger.debug(f"Processing row {i+1}, asset: {asset_name}")

                if asset_name.startswith("#"):
                    logger.info(f"Found potential target asset: {asset_name}")
                    entry = asset_name.split("-")
                    if len(entry) == 3:
                        asset_label, ticker, count_str = entry
                        try:
                            #stock_count = int(count_str)
                            stock_count = float(count_str)
                        except ValueError:
                            logger.warning(f"Could not parse count from asset name: {asset_name}")
                            continue # Skip to next row

                        # Get historical data for the specific target_date_str
                        # ソースはprefetchと同じ（通常はキャッシュヒットするため直接呼ばれない）
                        if self.stock_price_source == "yfinance":
                            hist_stock_price = self.get_historical_stock_price_yf(ticker, target_date_str)
                        else:
                            hist_stock_price = self.get_historical_stock_price(ticker, target_date_str)
                        hist_exchange_rate = self.get_historical_exchange_rate(target_date_str)

                        calculated_value = None
                        use_previous_value = False

                        if hist_stock_price is not None and hist_exchange_rate is not None:
                            # Data found, calculate normally
                            calculated_value = int(hist_stock_price * hist_exchange_rate * stock_count)
                            logger.info(f"Calculated historical value for {asset_name} on {target_date_str}: {calculated_value} JPY")
                            # Store the newly calculated value
                            self.last_calculated_values[asset_name] = calculated_value
                        else:
                            # Data missing, try using the last known value
                            logger.warning(f"Missing historical price/rate data for {asset_name} on {target_date_str}.")
                            if asset_name in self.last_calculated_values:
                                calculated_value = self.last_calculated_values[asset_name]
                                use_previous_value = True
                                logger.warning(f"Using previous day's value for {asset_name}: {calculated_value} JPY")
                            else:
                                logger.error(f"Skipping update for {asset_name} on {target_date_str}: Missing data and no previous value available.")
                                continue # Skip this asset for this date

                        # Proceed only if we have a value (either current or previous)
                        if calculated_value is not None:
                            # Find and click the pencil icon to enable editing
                            try:
                                edit_icon_xpath = "./td[contains(@class, 'amount-editable')]//i[contains(@class, 'icon-pencil')]"
                                edit_icon = row.find_element(By.XPATH, edit_icon_xpath)
                                logger.debug(f"Attempting to click edit icon for {asset_name} using JavaScript.")
                                # Use JavaScript click to potentially bypass interception issues
                                self.driver.execute_script("arguments[0].click();", edit_icon)
                                logger.debug(f"JavaScript click executed for edit icon of {asset_name}")
                                time.sleep(1.0) # Increase pause slightly after JS click
                            except NoSuchElementException:
                                logger.warning(f"Could not find edit icon for {asset_name} using XPath: {edit_icon_xpath}. Skipping.")
                                continue

                            # Wait for the input field to become clickable after clicking the edit icon
                            # Search globally using a more specific XPath anchored to the asset name in the row
                            try:
                                # Construct XPath to find the input within the row containing the specific asset name
                                value_input_xpath = f"//tr[contains(normalize-space(.), '{asset_name}')]//input[@name='user_asset_sum_hst[value]']"
                                logger.debug(f"Waiting for input field using XPath: {value_input_xpath}")
                                # Wait specifically for this element to be clickable, potentially longer timeout
                                value_input = WebDriverWait(self.driver, 20).until(
                                    # Wait for the element to be present in the DOM first
                                    ec.presence_of_element_located((By.XPATH, value_input_xpath))
                                )
                                # Find the element without necessarily waiting for clickability
                                value_input = self.driver.find_element(By.XPATH, value_input_xpath)
                                logger.debug(f"Found value input field for {asset_name} in DOM.")

                                # Use JavaScript to set the value directly
                                logger.debug(f"Attempting to set value '{calculated_value}' using JavaScript for {asset_name}")
                                self.driver.execute_script("arguments[0].value = arguments[1];", value_input, str(calculated_value))
                                logger.debug(f"Value set via JavaScript for {asset_name}")
                                time.sleep(0.5) # Small pause after JS execution

                                # Send Enter key to submit the form (since no save button)
                                # Ensure the element is interactable before sending Enter
                                value_input = WebDriverWait(self.driver, 10).until(
                                    ec.element_to_be_clickable((By.XPATH, value_input_xpath))
                                )
                                value_input.send_keys(Keys.RETURN) # Use imported Keys
                                logger.info(f"Sent Enter key to submit update for {asset_name}")

                                # Optional: Add a short wait and potentially verify the update
                                time.sleep(3) # Wait for potential page update/AJAX completion
                                # Verification could involve checking if the displayed value matches calculated_value
                                # or if the input field is hidden again. This can be complex.
                                if use_previous_value:
                                     logger.info(f"Update attempted for {asset_name} for {target_date_str} using previous value.")
                                else:
                                     logger.info(f"Update attempted for {asset_name} for {target_date_str}.")


                            except (NoSuchElementException, TimeoutException) as e:
                                logger.error(f"Could not find/interact with value input field for {asset_name} after clicking edit: {e}")
                                self.print_html_and_screenshot(f"history_input_error_{asset_label}_{ticker}_{target_date_str}")
                                continue # Skip to next row
                            except Exception as e_inner:
                                logger.error(f"Error during value input/submit for {asset_name}: {e_inner}")
                                self.print_html_and_screenshot(f"history_submit_error_{asset_label}_{ticker}_{target_date_str}")
                                continue
                        # This else block is now handled above where calculated_value is checked
                        # else:
                        #     logger.warning(f"Skipping update for {asset_name} on {target_date_str} due to missing historical price/rate data.")
                    else:
                        logger.warning(f"Could not parse ticker/count from asset name: {asset_name}")

            except NoSuchElementException:
                logger.debug(f"Skipping row {i+1}, could not find expected asset name element.")
                continue
            except Exception as e:
                logger.error(f"Error processing row {i+1} (Asset: {asset_name}) on {target_date_str}: {e}")
                self.print_html_and_screenshot(f"history_row_error_{i+1}_{target_date_str}")
                continue # Continue to next row even if one fails
    # close method remains the same
    def close(self):
        """Closes the selenium webdriver."""
        if hasattr(self, 'driver') and self.driver:
            try:
                # Try closing the current window first
                self.driver.close()
            except Exception:
                logger.debug("Ignore exception during driver.close()")
            try:
                # Quit the entire driver instance
                self.driver.quit()
                logger.info("Selenium driver quit.")
            except Exception:
                logger.debug("Ignore exception during driver.quit()")
    # print_html_and_screenshot remains the same
    def print_html_and_screenshot(self, filename_prefix="debug"):
        """Helper function to save HTML and screenshot for debugging."""
        # Ensure driver exists before trying to use it
        if not hasattr(self, 'driver') or not self.driver:
            logger.error("Cannot save debug info: Selenium driver not initialized.")
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        html_filename = f"{filename_prefix}_{timestamp}.html"
        png_filename = f"{filename_prefix}_{timestamp}.png"
        try:
            # Check if driver has a window handle (might be closed already)
            if self.driver.window_handles:
                html_content = self.driver.page_source
                with open(html_filename, "w", encoding="utf-8") as f:
                    f.write(html_content)
                logger.info(f"Saved HTML source to {html_filename}")

                # save_screenshot might fail if the window is already closed
                if self.driver.window_handles:
                     self.driver.save_screenshot(png_filename)
                     logger.info(f"Saved screenshot to {png_filename}")
                else:
                    logger.warning("Could not save screenshot, window might be closed.")
            else:
                 logger.warning("Could not save HTML/screenshot, no active window handles.")

        except Exception as e:
            logger.error(f"Failed to save debug info: {e}")
    # send_to_element remains the same (though less used now)
    # --- Helper methods copied/adapted from mf.py ---
    def send_to_element(self, xpath, keys):
        """Sends keys to an element found by XPath."""
        try:
            # Use presence_of_element_located for finding, then check interactability before sending keys
            element = self.wait.until(ec.presence_of_element_located((By.XPATH, xpath)))
            # Ensure it's clickable before interacting
            element = self.wait.until(ec.element_to_be_clickable((By.XPATH, xpath)))
            element.clear()
            element.send_keys(keys)
            logger.debug(f"Sent keys to element: {xpath}")
        except (NoSuchElementException, TimeoutException) as e:
            logger.error(f"Could not find or interact with element {xpath}: {e}")
            # Avoid calling screenshot here if driver might be closing
            # self.print_html_and_screenshot(f"send_keys_error_{xpath.replace('/', '_')}")
            raise # Re-raise the exception

    def getCurrentGroup(self) -> str:
        """現在選択されているグループ名を取得します。"""
        group_dropdown_id = "group_id_hash"                                    # ドロップダウンのID
        try:
            logger.debug("現在のグループを確認中... 🔍")
            # ドロップダウン要素を待機して取得
            group_dropdown_element = self.wait.until(
                ec.presence_of_element_located((By.ID, group_dropdown_id))
            )
            # Selectオブジェクトを作成
            select = Select(group_dropdown_element)
            # 選択されているオプションのテキストを取得
            current_group = select.first_selected_option.text
            logger.info(f"現在のグループ: {current_group} ✨")
            return current_group

        except NoSuchElementException:
            logger.error(f"グループ選択ドロップダウン (ID: {group_dropdown_id}) が見つかりません 😢")
            return None
        except TimeoutException:
            logger.error(f"グループ選択ドロップダウン (ID: {group_dropdown_id}) の待機がタイムアウトしました 🕒")
            return None
        except Exception as e:
            logger.error(f"グループ取得中にエラーが発生しました: {type(e).__name__} - {e} 🚨")
            return None

    def choseGroup(self, nameOfGroup="グループ選択なし"):
        # --- グループ選択処理 (開始前) ---
        group_dropdown_id = "group_id_hash" # ドロップダウンのIDを修正
        try:
            logger.info(f"Selecting '{nameOfGroup}'...")
            group_dropdown_element = self.wait.until(ec.presence_of_element_located((By.ID, group_dropdown_id)))
            select = Select(group_dropdown_element)
            select.select_by_visible_text(nameOfGroup)
            logger.info(f"'{nameOfGroup}' selected.")
            # グループ変更後のページ更新/要素再描画を待機 (より堅牢な待機条件があれば変更)
            time.sleep(3) # 少し長めの待機時間に変更
            self.wait.until(ec.presence_of_all_elements_located) # 念のため要素が表示されるまで待つ
        except NoSuchElementException:
            logger.error(f"Group selection dropdown (ID: {group_dropdown_id}) not found. Aborting portfolio update.")
            return # メソッドを中断
        except TimeoutException:
                logger.error(f"Group selection dropdown (ID: {group_dropdown_id}) not found within timeout. Aborting portfolio update.")
                return # メソッドを中断
        except Exception as e:
            logger.error(f"Error selecting '{nameOfGroup}': {type(e).__name__} - {e}. Aborting portfolio update.")
            return # メソッドを中断
        # --- グループ選択処理 (ここまで) ---
    
if __name__ == "__main__":
    # Need to import Keys for sending Enter
    # from selenium.webdriver.common.keys import Keys # Already imported at top level

    args, start_date_obj, end_date_obj = parse_args() # Get parsed args and date objects
    if "LOG_LEVEL" in os.environ:
        # Attempt to convert log level, default to INFO on error
        try:
            log_level = int(os.environ["LOG_LEVEL"])
            logzero.loglevel(log_level)
        except ValueError:
             logger.warning(f"Invalid LOG_LEVEL env var: {os.environ['LOG_LEVEL']}. Defaulting to INFO.")
             logzero.loglevel(logzero.INFO)
    else:
        logzero.loglevel(logzero.INFO) # Default log level

    editor = None # Initialize editor to None
    overall_success = True # Track if all dates were processed without critical errors

    try:
        # Create editor instance once
        editor = MoneyForwardEditor()
        editor.init_selenium()

        editor.login() # Login once
        currentGroup = editor.getCurrentGroup()
        editor.choseGroup("グループ選択なし")

        # 全銘柄・全期間の株価を処理開始前に一括取得（yfinance APIコールを最小化）
        tickers = editor.get_tickers_from_page(start_date_obj)
        if tickers:
            logger.info(f"Found tickers to prefetch: {tickers}")
            editor.prefetch_stock_prices(tickers, start_date_obj, end_date_obj)
        else:
            logger.warning("No tickers found for prefetch. Will fetch per-day as fallback.")

        # Iterate through the date range
        current_date = start_date_obj
        while current_date <= end_date_obj:
            logger.info(f"===== Processing date: {current_date.strftime('%Y-%m-%d')} =====")
            try:
                # Call edit_history for the current date
                editor.edit_history(current_date)
                logger.info(f"Finished processing for date {current_date.strftime('%Y-%m-%d')}.")
            except NotImplementedError as nie:
                 logger.error(f"Execution stopped for date {current_date.strftime('%Y-%m-%d')}: {nie}")
                 overall_success = False
                 break # Stop processing further dates if 2FA is needed but not implemented
            except (ValueError, NoSuchElementException, TimeoutException) as ve:
                 # Catch specific errors during history editing for this date
                 logger.error(f"Execution failed for date {current_date.strftime('%Y-%m-%d')}: {ve}")
                 overall_success = False # Mark as failed but continue to next date
            except Exception as e:
                # Catch unexpected errors for this date
                logger.exception(f"An unexpected error occurred processing date {current_date.strftime('%Y-%m-%d')}: {e}")
                overall_success = False # Mark as failed but continue to next date

            # Move to the next day
            current_date += datetime.timedelta(days=1)
            time.sleep(1) # Small delay between dates

    except NotImplementedError as nie:
         # Error during initial setup/login (e.g., 2FA needed)
         logger.error(f"Execution stopped during setup: {nie}")
         overall_success = False
    except (ValueError, NoSuchElementException, TimeoutException) as ve:
         # Error during initial setup/login
         logger.error(f"Execution failed during setup/login: {ve}")
         overall_success = False
    except Exception as e:
        # Unexpected error during setup/login
        logger.exception(f"An unexpected error occurred during setup/login: {e}")
        overall_success = False
    finally:
        if editor:
            editor.choseGroup(currentGroup)
            editor.close()
        logger.info("===== Processing complete =====")
        # Exit with appropriate code
        import sys
        sys.exit(0 if overall_success else 1)
