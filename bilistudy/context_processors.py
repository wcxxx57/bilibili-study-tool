"""
上下文处理器
为所有模板提供通用的上下文数据
"""

from .models import UserPreference

def user_preferences(request):
    """
    为所有模板提供用户偏好设置
    """
    context = {
        'user_preference': None
    }
    
    if request.user.is_authenticated:
        try:
            user_preference = UserPreference.objects.get(user=request.user)
            context['user_preference'] = user_preference
        except UserPreference.DoesNotExist:
            # 如果用户偏好不存在，创建默认设置
            user_preference = UserPreference.objects.create(
                user=request.user,
                enable_learning_reminder=True,
                enable_content_filter_reminder=True,
                theme_preference='light'
            )
            context['user_preference'] = user_preference
    
    return context
