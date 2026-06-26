"""
飞书通知模块
使用飞书开放平台 API 发送消息通知
- 获取 tenant_access_token
- 发送文本消息到指定用户
"""
import time
import requests
import threading

# 飞书应用配置
FEISHU_APP_ID = "cli_a940302afa78dcee"
FEISHU_APP_SECRET = "YOUR_FEISHU_SECRET_HERE"
FEISHU_USER_ID = "ou_3e92a0d2356df4ed2afd613bdc60d4a3"

# Token 缓存
_token_cache = {
    "token": None,
    "expires_at": 0,
}
_lock = threading.Lock()


def get_tenant_access_token():
    """获取飞书 tenant_access_token（带缓存）"""
    with _lock:
        now = time.time()
        if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
            return _token_cache["token"]

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()

        if data.get("code") == 0:
            token = data["tenant_access_token"]
            expire = data.get("expire", 7200)  # 默认 2 小时

            with _lock:
                _token_cache["token"] = token
                _token_cache["expires_at"] = now + expire - 120  # 提前 2 分钟刷新

            print(f"[Feishu] Token 获取成功, 有效期 {expire}s")
            return token
        else:
            print(f"[Feishu] Token 获取失败: {data}")
            return None
    except Exception as e:
        print(f"[Feishu] Token 请求异常: {e}")
        return None


def send_message(user_id, content):
    """
    发送文本消息给飞书用户
    
    Args:
        user_id: 用户 open_id 或 user_id
        content: 消息内容（支持纯文本）
    
    Returns:
        bool: 是否成功
    """
    token = get_tenant_access_token()
    if not token:
        print("[Feishu] 无法获取 Token，消息发送取消")
        return False

    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    payload = {
        "receive_id": user_id,
        "msg_type": "interactive",
        "content": _build_interactive_card(content),
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        data = resp.json()

        if data.get("code") == 0:
            print(f"[Feishu] 消息发送成功 → {user_id}")
            return True
        else:
            print(f"[Feishu] 消息发送失败: code={data.get('code')}, msg={data.get('msg')}")
            # 如果是 token 过期，清除缓存重试一次
            if data.get("code") in (99991663, 99991661):
                with _lock:
                    _token_cache["token"] = None
                token2 = get_tenant_access_token()
                if token2:
                    resp2 = requests.post(
                        url, json=payload,
                        headers={"Authorization": f"Bearer {token2}", "Content-Type": "application/json"},
                        timeout=10,
                    )
                    data2 = resp2.json()
                    if data2.get("code") == 0:
                        print(f"[Feishu] 消息发送成功(重试) → {user_id}")
                        return True
            return False
    except Exception as e:
        print(f"[Feishu] 消息发送异常: {e}")
        return False


def _build_interactive_card(content):
    """构建飞书交互消息卡片"""
    import json

    # 保留原始文本中的换行拆分
    lines = content.strip().split("\n")
    title = lines[0] if lines else "预警通知"
    body_lines = lines[1:] if len(lines) > 1 else lines

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 StockMonitor 股票预警"},
            "template": "red",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(body_lines),
                },
            },
            {
                "tag": "hr",
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')} · 来自 StockMonitor",
                    }
                ],
            },
        ],
    }

    return json.dumps(card, ensure_ascii=False)


def send_stock_alert(rule_name, stock_name, stock_code, alert_type, alert_msg):
    """
    发送股票预警通知（封装函数）
    
    同时发送纯文本和卡片消息
    """
    # 先尝试卡片消息（通过 interactive 类型）
    card_content = f"""⚠️ **{alert_type}**

**规则**: {rule_name}
**股票**: {stock_name} ({stock_code})

**详情**: {alert_msg}"""

    success = send_message(FEISHU_USER_ID, card_content)

    if not success:
        # 降级为纯文本
        text_msg = f"[StockMonitor预警] {alert_type}\n规则: {rule_name}\n{stock_name}({stock_code}): {alert_msg}"
        success = _send_text_message(FEISHU_USER_ID, text_msg)

    return success


def _send_text_message(user_id, content):
    """发送纯文本消息（降级方案）"""
    token = get_tenant_access_token()
    if not token:
        return False

    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    payload = {
        "receive_id": user_id,
        "msg_type": "text",
        "content": '{"text":"' + content.replace('"', '\\"').replace('\n', '\\n') + '"}',
    }

    try:
        resp = requests.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            print(f"[Feishu] 文本消息发送成功")
            return True
        print(f"[Feishu] 文本消息发送失败: {data}")
        return False
    except Exception as e:
        print(f"[Feishu] 文本消息异常: {e}")
        return False


if __name__ == "__main__":
    # 测试发送
    result = send_stock_alert(
        rule_name="价格突破",
        stock_name="贵州茅台",
        stock_code="600519",
        alert_type="price_up",
        alert_msg="价格 1200 突破 1180 元阈值",
    )
    print("发送结果:", result)
