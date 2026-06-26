"""
认证蓝图 - JWT 身份认证 & 授权
POST /api/v1/auth/register    - 注册
POST /api/v1/auth/login       - 登录
POST /api/v1/auth/logout      - 登出
GET  /api/v1/auth/me          - 当前用户信息
"""
import jwt
import uuid
from functools import wraps
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, g

import database as db
from config import JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_HOURS

auth_bp = Blueprint('auth', __name__)


def generate_token(user_id, username, role):
    """生成JWT Token"""
    now = datetime.utcnow()
    exp = now + timedelta(hours=JWT_EXPIRE_HOURS)
    jti = uuid.uuid4().hex
    payload = {
        'sub': str(user_id),
        'username': username,
        'role': role,
        'iat': now,
        'exp': exp,
        'jti': jti,
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, jti, exp


def login_required(f):
    """JWT 认证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({"code": 40101, "msg": "未提供认证令牌", "error": "UNAUTHORIZED"}), 401
        
        token = auth_header[7:]
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            
            # 检查黑名单
            if db.is_token_blacklisted(payload['jti']):
                return jsonify({"code": 40102, "msg": "令牌已失效", "error": "TOKEN_BLACKLISTED"}), 401
            
            g.user_id = int(payload['sub'])
            g.username = payload['username']
            g.user_role = payload['role']
            g.token_jti = payload['jti']
            g.token_exp = payload['exp']
        except jwt.ExpiredSignatureError:
            return jsonify({"code": 40103, "msg": "令牌已过期", "error": "TOKEN_EXPIRED"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"code": 40104, "msg": "无效令牌", "error": "INVALID_TOKEN"}), 401
        
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """管理员权限装饰器"""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if g.user_role != 'admin':
            return jsonify({"code": 40301, "msg": "需要管理员权限", "error": "FORBIDDEN"}), 403
        return f(*args, **kwargs)
    return decorated


# ============ 接口 ============

@auth_bp.route('/register', methods=['POST'])
def register():
    """用户注册 POST /api/v1/auth/register"""
    data = request.get_json()
    if not data:
        return jsonify({"code": 40001, "msg": "请提供JSON数据", "error": "BAD_REQUEST"}), 400
    
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not username or len(username) < 3:
        return jsonify({"code": 40002, "msg": "用户名至少3个字符", "error": "VALIDATION_ERROR"}), 400
    if not password or len(password) < 6:
        return jsonify({"code": 40003, "msg": "密码至少6个字符", "error": "VALIDATION_ERROR"}), 400
    
    user_id = db.create_user(username, password)
    if user_id is None:
        return jsonify({"code": 40901, "msg": "用户名已存在", "error": "CONFLICT"}), 409
    
    token, jti, exp = generate_token(user_id, username, 'user')
    return jsonify({
        "code": 0,
        "msg": "注册成功",
        "data": {
            "user_id": user_id,
            "username": username,
            "role": "user",
            "token": token,
            "token_type": "Bearer",
            "expires_at": exp.isoformat(),
        }
    })


@auth_bp.route('/login', methods=['POST'])
def login():
    """用户登录 POST /api/v1/auth/login"""
    data = request.get_json()
    if not data:
        return jsonify({"code": 40001, "msg": "请提供JSON数据", "error": "BAD_REQUEST"}), 400
    
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not username or not password:
        return jsonify({"code": 40004, "msg": "用户名和密码不能为空", "error": "VALIDATION_ERROR"}), 400
    
    user = db.verify_user(username, password)
    if not user:
        return jsonify({"code": 40105, "msg": "用户名或密码错误", "error": "AUTH_FAILED"}), 401
    
    token, jti, exp = generate_token(user['id'], user['username'], user['role'])
    return jsonify({
        "code": 0,
        "msg": "登录成功",
        "data": {
            "user_id": user['id'],
            "username": user['username'],
            "role": user['role'],
            "token": token,
            "token_type": "Bearer",
            "expires_at": exp.isoformat(),
        }
    })


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """用户登出 POST /api/v1/auth/logout"""
    exp_dt = datetime.fromtimestamp(g.token_exp)
    db.blacklist_token(g.token_jti, exp_dt.strftime('%Y-%m-%d %H:%M:%S'))
    return jsonify({"code": 0, "msg": "已登出"})


@auth_bp.route('/me', methods=['GET'])
@login_required
def me():
    """获取当前用户信息 GET /api/v1/auth/me"""
    user = db.get_user_by_id(g.user_id)
    if not user:
        return jsonify({"code": 40401, "msg": "用户不存在", "error": "NOT_FOUND"}), 404
    
    return jsonify({
        "code": 0,
        "data": {
            "user_id": user['id'],
            "username": user['username'],
            "role": user['role'],
            "created_at": user['created_at'],
            "last_login": user['last_login'],
        }
    })
