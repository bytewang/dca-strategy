"""
513300（华夏纳斯达克100ETF）定投策略 - 企业微信通知版
功能：
  1. 每月19-21号之间第一个交易日自动判断
  2. 生成买入建议后通过企业微信机器人发送
  3. 非定投日自动跳过，不发送通知
  4. 密钥通过环境变量 WECOM_KEY 传入，安全无泄露
数据源：TickFlow（前复权）
"""

import os
import pandas as pd
import numpy as np
import requests
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')


# ==================== 1. 企业微信通知 ====================

def send_wecom_message(content):
    """通过企业微信机器人发送消息，密钥从环境变量读取"""
    headers = {'Content-Type': 'application/json'}
    data = {
        "msgtype": "text",
        "text": {"content": content}
    }
    
    # 从环境变量读取 Webhook Key
    webhook_key = os.getenv('WECOM_KEY')
    if not webhook_key:
        print("❌ 错误：未找到 WECOM_KEY 环境变量，请先设置！")
        return False
    
    WEBHOOK_URL = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook_key}"
    
    try:
        response = requests.post(WEBHOOK_URL, headers=headers, json=data, timeout=10)
        result = response.json()
        if result.get('errcode') == 0:
            print("✅ 企业微信通知发送成功")
            return True
        else:
            print(f"❌ 企业微信通知发送失败: {result}")
            return False
    except Exception as e:
        print(f"❌ 企业微信通知发送异常: {str(e)}")
        return False


# ==================== 2. 数据获取（TickFlow） ====================

def get_data_from_tickflow(symbol="513300.SH", count=10000, adjust="forward"):
    """使用 TickFlow 获取历史数据"""
    try:
        from tickflow import TickFlow
        
        print(f"📥 正在从 TickFlow 下载 {symbol} 数据...")
        tf = TickFlow.free()
        
        df = tf.klines.get(
            symbol, 
            period="1d", 
            count=count, 
            adjust=adjust,
            as_dataframe=True
        )
        
        if df is None or len(df) == 0:
            print("❌ 未获取到数据")
            return None
        
        # 日期处理
        if 'trade_date' in df.columns:
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df = df.set_index('trade_date')
        elif 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
        elif 'time_key' in df.columns:
            df['time_key'] = pd.to_datetime(df['time_key'])
            df = df.set_index('time_key')
        else:
            try:
                df.index = pd.to_datetime(df.index)
            except:
                pass
        
        # 价格列处理
        if 'adj_close' in df.columns:
            df = df.rename(columns={'adj_close': 'price'})
        elif 'close' in df.columns:
            df = df.rename(columns={'close': 'price'})
        elif 'Close' in df.columns:
            df = df.rename(columns={'Close': 'price'})
        else:
            numeric_cols = df.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 0:
                df['price'] = df[numeric_cols[0]]
            else:
                print("❌ 未找到价格列")
                return None
        
        df = df[['price']].sort_index()
        df = df[~df.index.duplicated(keep='first')]
        df = df[df.index.year > 2000]
        
        if len(df) == 0:
            print("❌ 过滤后无有效数据")
            return None
        
        print(f"✅ 数据获取成功，共 {len(df)} 个交易日")
        print(f"   最新价格: {df['price'].iloc[-1]:.4f} 元")
        
        return df
        
    except ImportError:
        print("❌ 未安装 tickflow，请执行: pip install tickflow")
        return None
    except Exception as e:
        print(f"❌ 获取数据失败: {str(e)}")
        return None


# ==================== 3. 计算估值百分位 ====================

def calc_price_rank(df):
    """计算价格的历史百分位（expanding 窗口，无未来数据泄露）"""
    df = df.copy()
    df['price_rank'] = df['price'].expanding().rank(pct=True)
    return df


# ==================== 4. 获取当月的定投日 ====================

def get_investment_day(year, month):
    """
    获取当月定投日（19-21号之间的第一个交易日）
    返回：(定投日日期, 是否为有效定投日)
    """
    for day in [19, 20, 21]:
        try:
            d = datetime(year, month, day).date()
            if d.weekday() < 5:  # 周一至周五
                return d, True
        except ValueError:
            pass
    return None, False


def is_today_investment_day(check_date=None):
    """判断今天是否应该定投"""
    if check_date is None:
        check_date = datetime.now().date()
    
    year = check_date.year
    month = check_date.month
    
    invest_day, has_invest_day = get_investment_day(year, month)
    
    if not has_invest_day:
        return False, None, "本月19-21号之间无交易日"
    
    if check_date != invest_day:
        return False, invest_day, f"定投日为{invest_day.day}号，今日{check_date.day}号，跳过"
    
    return True, invest_day, "定投日"


# ==================== 5. 生成买入建议 ====================

def get_investment_advice(df, monthly_amount=5000):
    """根据最新数据生成买入建议"""
    if df is None or len(df) == 0:
        return {
            'success': False,
            'message': '❌ 无法获取数据'
        }
    
    is_today, invest_day, reason = is_today_investment_day()
    
    if not is_today:
        return {
            'success': False,
            'skip': True,
            'invest_day': invest_day,
            'reason': reason
        }
    
    df_with_rank = calc_price_rank(df)
    latest = df_with_rank.iloc[-1]
    latest_price = latest['price']
    latest_rank = latest['price_rank']
    
    # 三级阶梯策略
    if latest_rank < 0.3:
        multiplier = 3
        level = "🔴 极度低估"
        advice_text = "建议买入 3 倍（低位加倍！）"
        amount = monthly_amount * 3
    elif latest_rank < 0.5:
        multiplier = 2
        level = "🟠 相对低估"
        advice_text = "建议买入 2 倍"
        amount = monthly_amount * 2
    elif latest_rank > 0.8:
        multiplier = 0.8
        level = "🟢 相对高估"
        advice_text = "建议买入 0.8 倍（高位控制）"
        amount = monthly_amount * 0.8
    else:
        multiplier = 1
        level = "🟡 估值合理"
        advice_text = "建议买入 1 倍"
        amount = monthly_amount
    
    # 生成企业微信消息内容
    message = f"""📊 513300 纳斯达克ETF华夏 月度定投提醒
📅 定投日期: {invest_day.strftime('%Y-%m-%d')}
💰 最新价格: {latest_price:.4f} 元
📈 历史分位: {latest_rank:.1%}
📌 估值状态: {level}
{'='*30}
💡 本月建议: {advice_text}
💰 建议金额: {amount:,.0f} 元
📊 倍数: {multiplier}x"""
    
    return {
        'success': True,
        'invest_day': invest_day,
        'price': latest_price,
        'price_rank': latest_rank,
        'multiplier': multiplier,
        'level': level,
        'advice_text': advice_text,
        'amount': amount,
        'message': message
    }


# ==================== 6. 主程序 ====================

def main():
    print("=" * 55)
    print("513300 月度定投策略提醒（企业微信版）")
    print("=" * 55)
    
    today = datetime.now().date()
    weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    print(f"📅 当前日期: {today.strftime('%Y-%m-%d')} ({weekday_names[today.weekday()]})")
    
    # 获取数据
    df = get_data_from_tickflow("513300.SH", count=10000, adjust="forward")
    
    if df is None or len(df) == 0:
        print("❌ 无法获取数据，程序退出")
        return
    
    # 生成建议
    result = get_investment_advice(df, monthly_amount=5000)
    
    if not result.get('success', False):
        if result.get('skip', False):
            print(f"\n⏭️ {result.get('reason', '跳过')}")
            if result.get('invest_day'):
                print(f"   📌 本月定投日: {result['invest_day'].strftime('%Y-%m-%d')}")
            print("SKIP: 今日不是定投日")
        else:
            print(f"\n❌ {result.get('message', '未知错误')}")
        return
    
    # 输出到控制台
    print("\n" + "=" * 55)
    print(result['message'])
    print("=" * 55)
    
    # 通过企业微信发送
    print("\n📤 正在发送企业微信通知...")
    send_wecom_message(result['message'])


# ==================== 7. 程序入口 ====================

if __name__ == "__main__":
    main()
