from logzero import logger
import logzero

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select # Selectクラスをインポート
from selenium.webdriver.support import expected_conditions as ec
# Import specific exceptions
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    NoSuchElementException
)

import requests

import os, time, datetime
import imaplib, email, re, pyotp, pytz
import email.header # 追加
import email.utils # 追加


class MoneyForward:
    def __init__(self) -> None:
        self.stock_price_cache: dict[str, float] = dict()

    def init(self):
        logger.info("selenium initializing...")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless") # Restore headless mode
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
        # options.binary_location = "/usr/bin/chromium-browser" # macOSでは不要なためコメントアウト
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 30) # Increase default wait time to 30 seconds
        self.driver.implicitly_wait(10)
        if not "ALPHAVANTAGE_API_KEY" in os.environ:
            raise ValueError("env ALPHAVANTAGE_API_KEY is not found.")
        self.alphavantage_apikey = os.environ["ALPHAVANTAGE_API_KEY"]

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

    def portfolio(self):
        try:
            usdrate = self.usdrate()
        except Exception as e:
            logger.warning(f"Warning fetching USD/JPY rate: {type(e).__name__} - {e}")
            usdrate = 123.456 # for DEBUG  
            self.stock_price_cache['AAPL'] = 154.32 # for DEBUG 
            self.stock_price_cache['ACWI'] = 123.45 # for DEBUG 

        logger.info("USDJPY: " + str(usdrate))
        try: # ★追加: ページ読み込みと待機処理をtryで囲む
            logger.debug("Navigating to portfolio page...") # ★追加: デバッグログ
            self.driver.get("https://moneyforward.com/bs/portfolio")
            logger.debug("Waiting for portfolio page elements to load...") # ★追加: デバッグログ
            self.wait.until(ec.presence_of_all_elements_located)
            logger.debug("Portfolio page elements loaded.") # ★追加: デバッグログ
        except Exception as page_load_err: # ★追加: 例外を捕捉
            logger.error(f"Failed to load or wait for portfolio page: {type(page_load_err).__name__} - {page_load_err}", exc_info=True)
            # ★追加: ページ読み込みに失敗したらメソッドを中断
            return

        # テーブル要素を特定するXPath (株式(現物)テーブル)
        table_xpath = '//*[@id="portfolio_det_eq"]/table/tbody'
        try:
            # テーブルが表示されるまで待つ
            self.wait.until(ec.presence_of_element_located((By.XPATH, table_xpath)))
            elements = self.driver.find_elements(by=By.XPATH, value=f'{table_xpath}/tr')
            logger.info(f"Found {len(elements)} rows in the equities table.")
        except TimeoutException:
            logger.error("Equities table not found after selecting group. Cannot proceed.")
            elements = [] # 空リストにしてループをスキップ

        # ポートフォリオ更新処理ループ
        for i in range(len(elements)):
            tds = elements[i].find_elements(by=By.TAG_NAME, value="td")
            name = tds[1].text
            #logger.info(f"elements at {i}: {name}")
            if name.startswith("#"): # ★ name[0:1] == "#" を変更
                entry = name.split("-")
                if len(entry) < 3:
                     logger.warning(f"Invalid format in name '{name}'. Skipping.")
                     continue
                stock_price = self.stock_price(entry[1])
                if stock_price is None:
                    logger.warning(f"Could not get stock price for {entry[1]}. Skipping update for {name}.")
                    continue
                try: # stock_count の変換エラーも考慮
                    stock_count = float(entry[2])
                except ValueError:
                    logger.warning(f"Invalid stock count format for {name}: {entry[2]}. Skipping.")
                    continue

                logger.info(f"{entry[0]}: {entry[1]} is {stock_price} USD ({int(usdrate * stock_price)} JPY) x {stock_count}") # ★ f-string に変更

                try:
                    # --- 要素操作開始 ---
                    img = tds[11].find_element(by=By.TAG_NAME, value="img")
                    self.driver.execute_script("arguments[0].click();", img) # 画像をクリック

                    det_value_id = "user_asset_det_value"
                    commit_name = "commit"

                    # ★修正点1: 入力欄が表示され、クリック可能になるまで待機！
                    det_value = self.wait.until(
                        ec.element_to_be_clickable((By.ID, det_value_id))
                    )
                    logger.debug(f"Element {det_value_id} is clickable for {name}")

                    # ★修正点2: 送信処理のリトライ（time.sleep削除）
                    send_success = False
                    for retry_count in range(2): # 2回試行
                        try:
                            self.send_to_element_direct(det_value, str(int(usdrate * stock_price) * stock_count))
                            send_success = True
                            logger.debug(f"Attempt {retry_count + 1}: send_keys successful for {name}")
                            break # 成功！
                        except StaleElementReferenceException: # 要素が古くなった場合
                             logger.warning(f"Attempt {retry_count + 1}: Stale element for det_value. Re-finding.")
                             try: # 要素を再検索して待機
                                 det_value = self.wait.until(ec.element_to_be_clickable((By.ID, det_value_id)))
                             except Exception as find_err:
                                 logger.error(f"Failed to re-find clickable det_value: {find_err}")
                                 break # 再検索失敗なら諦める
                        except Exception as send_err: # その他の送信エラー (ElementNotInteractableも含む)
                            logger.warning(f"Attempt {retry_count + 1} send_keys failed: {send_err}")
                            if retry_count < 1: time.sleep(1) # 少し待ってリトライ

                    if not send_success:
                        logger.error(f"Failed to send keys for {name}. Skipping commit.")
                        continue # 送信失敗なら次へ

                    # ★修正点3: コミットボタンもクリック可能になるまで待機！
                    commit = self.wait.until(
                        ec.element_to_be_clickable((By.NAME, commit_name))
                    )
                    logger.debug(f"Element {commit_name} is clickable for {name}")

                    # ★修正点4: クリック処理のリトライ（time.sleep削除）
                    click_success = False
                    for retry_count in range(2): # 2回試行
                        try:
                            commit.click()
                            click_success = True
                            logger.debug(f"Attempt {retry_count + 1}: commit click successful for {name}")
                            break # 成功！
                        except StaleElementReferenceException:
                             logger.warning(f"Attempt {retry_count + 1}: Stale element for commit. Re-finding.")
                             try:
                                 commit = self.wait.until(ec.element_to_be_clickable((By.NAME, commit_name)))
                             except Exception as find_err:
                                 logger.error(f"Failed to re-find clickable commit: {find_err}")
                                 break
                        except Exception as click_err:
                            logger.warning(f"Attempt {retry_count + 1} commit click failed: {click_err}")
                            if retry_count < 1: time.sleep(1)

                    if click_success:
                        time.sleep(1) # 更新反映を少し待つ
                        logger.info(f"{entry[0]} is updated.")
                    else:
                        logger.error(f"Failed to click commit for {name}.")
                    # --- 要素操作終了 ---

                except (NoSuchElementException, TimeoutException) as el_err:
                     logger.error(f"Could not find or wait for element for {name}: {el_err}. Skipping update.")
                     continue # 要素が見つからない/待機タイムアウトなら次へ
                except StaleElementReferenceException as stale_err:
                    logger.warning(f"Stale element encountered during update process for {name}: {stale_err}. Skipping update.")
                    continue # 処理中に要素が古くなったら次へ
                except Exception as e:
                     logger.error(f"Unexpected error during update for {name}: {e}", exc_info=True)
                     continue # 予期せぬエラーでも次へ


            # ループの最後での要素再取得は不要 (ループ先頭で取得しているため)
            # elements = self.driver.find_elements(by=By.XPATH, value='//*[@id="portfolio_det_eq"]/table/tbody/tr')

    def stock_price(self, tick):
        # Check cache first
        if tick in self.stock_price_cache:
            return self.stock_price_cache[tick]

        # Fetch from API if not in cache
        for retry in range(3):
            try:
                r = requests.get(f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={tick}&apikey={self.alphavantage_apikey}")
                r.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                data = r.json()
                if "Global Quote" in data and "05. price" in data["Global Quote"]:
                    price = float(data["Global Quote"]["05. price"])
                    self.stock_price_cache[tick] = price
                    return price
                elif "Note" in data:
                     logger.warning(f"API Note for {tick}: {data['Note']}")
                     # If it's an API limit note, maybe wait and retry or stop for this tick
                     if "call frequency" in data['Note'].lower():
                         logger.warning("API call frequency limit likely reached. Waiting before next attempt...")
                         time.sleep(60) # Wait 60 seconds if limit hit
                         continue # Retry after waiting
                     else:
                         return None # Other notes might indicate no data found
                else:
                    logger.warning(f"Unexpected API response for {tick}: {data}")
                    return None # Could not find price in response
            except requests.exceptions.RequestException as req_err:
                logger.error(f"Request failed for {tick} (attempt {retry+1}/3): {req_err}")
                if retry < 2: time.sleep(5) # Wait before retrying network errors
            except ValueError as json_err: # Includes JSONDecodeError
                 logger.error(f"Failed to parse JSON response for {tick}: {json_err}")
                 return None # Cannot proceed if JSON is invalid
            except Exception as e:
                 logger.error(f"Unexpected error fetching price for {tick}: {e}", exc_info=True)
                 return None # Return None on other unexpected errors

        logger.error(f"Failed to retrieve stock price for {tick} after 3 attempts.")
        return None # Return None if all retries fail

    def usdrate(self):
        # Consider caching USD rate as well, maybe with a short TTL
        try:
            r = requests.get(f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=JPY&apikey={self.alphavantage_apikey}")
            r.raise_for_status()
            data = r.json()
            if "Realtime Currency Exchange Rate" in data and "5. Exchange Rate" in data["Realtime Currency Exchange Rate"]:
                return float(data["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
            else:
                 logger.error(f"Could not find exchange rate in API response: {data}")
                 raise ValueError("Failed to retrieve USD/JPY exchange rate from API.")
        except requests.exceptions.RequestException as req_err:
            logger.error(f"Request failed for USD/JPY rate: {req_err}")
            raise ConnectionRefusedError("Failed to connect to currency exchange API.") from req_err
        except ValueError as json_err:
            logger.error(f"Failed to parse JSON response for USD/JPY rate: {json_err}")
            raise ValueError("Invalid JSON response from currency exchange API.") from json_err
        except Exception as e:
            logger.error(f"Unexpected error fetching USD/JPY rate: {e}", exc_info=True)
            raise RuntimeError("Unexpected error during USD/JPY rate fetch.") from e


    def close(self):
        # driver.close() は "cannot kill Chrome" エラーが発生するため削除
        # driver.quit() が成功すればセッションは終了される
        try:
            logger.info("Attempting to quit WebDriver...")
            self.driver.quit()
            logger.info("WebDriver quit successfully.")
        except Exception as e:
            logger.debug(f"Ignore exception (quit): {type(e).__name__} - {e}")

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

    def print_html(self):
        html = self.driver.execute_script("return document.getElementsByTagName('html')[0].innerHTML")
        print(html)

    def send_to_element(self, xpath, keys):
        element = self.driver.find_element(by=By.XPATH, value=xpath)
        element.clear()
        logger.debug("[send_to_element] " + xpath)
        element.send_keys(keys)

    def send_to_element_direct(self, element, keys):
        element.clear()
        logger.debug("[send_to_element] " + element.get_attribute("id"))
        element.send_keys(keys)


if __name__ == "__main__":
    if "LOG_LEVEL" in os.environ:
        logzero.loglevel(int(os.environ["LOG_LEVEL"]))
    mf = MoneyForward()
    try:
        mf.init()
        mf.login()
        #mf.choseGroup("グループ選択なし")
        mf.portfolio()
    finally:
        #mf.choseGroup("生活用")
        mf.close()
