import traceback
from utils.logger import setup_logger
from utils.config import get_config, get_userData
from core.msg_builder import build_message, build_message_with_openai
from core.browser import get_browser
from playwright.sync_api import Response
import time
import json


complates = {}

config = get_config()
userData = get_userData()
logger = setup_logger(level=config.get("logLevel", "Info"))
matchMode = config.get("matchMode", "nickname")
userIDDict = {}

def handle_response(response: Response):
    """
    只监听你要的那个接口响应
    """
    global userIDDict
    # 精准匹配目标接口 URL
    if "aweme/v1/creator/im/user_detail/" in response.url:
        # print(f"URL: {response.url}")
        # print(f"状态码: {response.status}")
        try:
            # 获取接口返回的 JSON 数据（就是你在 Network 里看到的内容）
            json_data = response.json()
            # print("\n📦 响应 JSON 数据：")
            # print(json.dumps(json_data, indent=4, ensure_ascii=False))
            for item in json_data.get("user_list", []):
                short_id = item.get("user", {}).get("ShortId")
                nickname = item.get("user", {}).get("nickname")
                user_id = item.get("user_id", "")
                userIDDict[str(short_id)] = {"nickname": nickname, "user_id": user_id}
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            last = tb[-1]
            print(f"解析响应失败: {e}")
            print(f"文件: {last.filename}, 行号: {last.lineno}, 函数: {last.name}")


def retry_operation(name, operation, retries=3, delay=2, *args, **kwargs):
    """
    通用的重试逻辑
    :param name: 操作名称（用于日志记录）
    :param operation: 要执行的异步操作
    :param retries: 最大重试次数
    :param delay: 每次重试之间的延迟（秒）
    :param args: 传递给操作的参数
    :param kwargs: 传递给操作的关键字参数
    """
    for attempt in range(retries):
        try:
            return operation(*args, **kwargs)
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"{name} 失败，正在重试第 {attempt + 1} 次，错误：{e}")
                time.sleep(delay)
            else:
                logger.error(f"{name} 失败，已达到最大重试次数，错误：{e}")
                raise


def scroll_and_select_user(page, username, targets):
    friends_tab_selector = 'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]'
    target_selector = 'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]//div[contains(@class, "semi-list-item-body semi-list-item-body-flex-start")]'
    scrollable_friends_selector = 'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]/div/div/div[3]/div/div/div/ul/div'
    no_more_selector = 'xpath=//div[contains(@class, "no-more-tip-")]'

    logger.debug(f"账号 {username} 点击进入好友标签页")
    page.wait_for_selector(friends_tab_selector)
    page.locator(friends_tab_selector).click()

    # 等待第一个好友出现
    first_friend_selector = 'xpath=//*[@id="sub-app"]/div/div/div[2]/div[2]/div/div/div[1]/div/div/div/ul/div/div/div[1]/li/div'
    page.wait_for_selector(first_friend_selector, timeout=15000)
    page.locator(first_friend_selector).click()
    time.sleep(1.5)

    found_targets = set()
    remaining_targets = set(targets)

    # ==================== 阶段一：滚动加载所有好友 ====================
    for _ in range(30):  # 增加滚动次数，建议根据好友数量调整（20~40）
        target_elements = page.locator(target_selector).all()

        for element in target_elements:
            try:
                span = element.locator("""xpath=.//span[contains(@class, "item-header-name-")]""")
                targetName = span.inner_text().strip()

                if targetName in found_targets:
                    continue

                found_targets.add(targetName)

                if matchMode == "short_id":
                    targetSymbol = next((sid for sid, info in userIDDict.items() if info.get("nickname") == targetName), None)
                else:
                    targetSymbol = targetName

                if targetSymbol and targetSymbol in remaining_targets:
                    # 找到目标后点击
                    element.click()
                    time.sleep(1.2)

                    # 发送消息
                    chat_input_selector = "xpath=//div[contains(@class, 'chat-input-')]"
                    page.wait_for_selector(chat_input_selector, timeout=10000)
                    chat_input = page.locator(chat_input_selector)

                    message = build_message()
                    for line in message.split("\\n"):
                        chat_input.type(line)
                        if line != message.split("\\n")[-1]:
                            chat_input.press("Shift+Enter")
                    chat_input.press("Enter")
                    time.sleep(2)

                    logger.info(f"账号 {username} 已发送消息给: {targetName}")

                    remaining_targets.remove(targetSymbol)

                    # 发送完后返回好友列表（重要！）
                    page.go_back()
                    time.sleep(2)
                    page.wait_for_selector(first_friend_selector, timeout=10000)

                    if len(remaining_targets) == 0:
                        logger.info(f"账号 {username} 所有目标好友已处理完成")
                        return

            except Exception as e:
                logger.warning(f"处理好友 {targetName} 时出错: {e}")
                continue

        # 滚动
        scrollable = page.locator(scrollable_friends_selector)
        if scrollable.count() > 0:
            scrollable.evaluate("(el) => el.scrollBy(0, 1200)")
            time.sleep(1.2)
        else:
            break

        # 到底检测
        if page.locator(no_more_selector).count() > 0:
            logger.info(f"账号 {username} 已到达列表底部")
            break

    if remaining_targets:
        logger.warning(f"账号 {username} 仍有未找到的好友: {remaining_targets}")

def do_user_task(browser, username, cookies, targets):
    context = browser.new_context()
    context.set_default_navigation_timeout(config["browserTimeout"])
    context.set_default_timeout(config["browserTimeout"])
    page = context.new_page()

    if matchMode == "short_id":
        page.on("response", handle_response)

    # 打开抖音创作者中心
    retry_operation(
        "打开抖音创作者中心",
        page.goto,
        retries=config["taskRetryTimes"],
        delay=5,
        url="https://creator.douyin.com/",
    )

    # 注入 Cookie
    context.add_cookies(cookies)

    # 导航到消息页面
    retry_operation(
        "导航到消息页面",
        page.goto,
        retries=config["taskRetryTimes"],
        delay=5,
        url="https://creator.douyin.com/creator-micro/data/following/chat",
    )

    logger.info(f"账号 {username} 开始处理 {len(targets)} 个目标好友")

    # ====================== 关键修改 ======================
    # 直接调用 scroll_and_select_user 即可
    # 不要再用 for 循环，因为发送逻辑已经在 scroll_and_select_user 里面了
    scroll_and_select_user(page, username, targets)

    logger.info(f"账号 {username} 任务完成")
    context.close()


def runTasks():
    playwright, browser = get_browser()
    try:
        # 检查是否启用多任务和任务数量
        # 创建信号量以限制并发任务数量
        logger.info("开始执行任务")
        logger.debug(f"当前配置如下：")
        logger.debug(f"消息模板: {config.get('messageTemplate', '未找到消息模板')}")
        logger.debug(f"一言类型: {config['hitokotoTypes']}")
        for user in userData:
            logger.debug(f"用户: {user.get('username', '未知用户')}, 目标好友: {user['targets']}")

        for user in userData:
            cookies = user["cookies"]
            targets = user["targets"]
            complates[user["unique_id"]] = []  # 初始化该用户的已完成列表
            username = user.get("username", "未知用户")
            logger.info(f"开始处理账号 {username}")
            # 创建任务
            do_user_task(browser, username, cookies, targets)
            logger.info(f"账号 {username} 任务完成")
    finally:
        # 关闭浏览器实例
        browser.close()
        
        playwright.stop()

        

