from django import template

register = template.Library()

@register.filter
def remainder(value, arg):
    """返回除法的余数"""
    return int(value) % int(arg)

@register.filter
def floordiv(value, divisor):
    """整数除法过滤器"""
    try:
        return int(value) // int(divisor)
    except (ValueError, ZeroDivisionError):
        return 0

@register.filter
def format_duration(seconds):
    """格式化时长为 HH:MM:SS 或 MM:SS 格式"""
    try:
        seconds = int(seconds)
        if seconds >= 3600:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            return f"{hours}:{minutes:02d}:{secs:02d}"
        elif seconds >= 60:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}:{secs:02d}"
        else:
            return f"{seconds}秒"
    except (ValueError, TypeError):
        return "0秒"