from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import random
import string

class BiliVideo(models.Model):
    """B站视频信息模型"""
    bvid = models.CharField(max_length=20, unique=True, verbose_name="BV号")
    title = models.CharField(max_length=200, verbose_name="标题")
    cover = models.URLField(verbose_name="封面URL")
    author = models.CharField(max_length=100, verbose_name="UP主")
    pub_date = models.DateField(verbose_name="发布日期")
    play_count = models.IntegerField(default=0, verbose_name="播放量")
    like_count = models.IntegerField(default=0, verbose_name="点赞数")
    description = models.TextField(blank=True, verbose_name="视频简介")
    
    def __str__(self):
        return self.title
    
    class Meta:
        verbose_name = "B站视频"
        verbose_name_plural = verbose_name

class VideoEpisode(models.Model):
    """视频分集模型"""
    video = models.ForeignKey(BiliVideo, on_delete=models.CASCADE, related_name='episodes', verbose_name="所属视频")
    cid = models.CharField(max_length=20, verbose_name="分集ID")
    title = models.CharField(max_length=200, verbose_name="分集标题")
    duration = models.IntegerField(default=0, verbose_name="时长(秒)")
    order = models.IntegerField(default=0, verbose_name="排序")
    
    def __str__(self):
        return f"{self.video.title} - {self.title}"
    
    class Meta:
        verbose_name = "视频分集"
        verbose_name_plural = verbose_name
        ordering = ['order']

class EmailVerification(models.Model):
    """邮箱验证码模型"""
    email = models.EmailField(verbose_name="邮箱")
    code = models.CharField(max_length=6, verbose_name="验证码")
    purpose = models.CharField(max_length=20, choices=[
        ('register', '注册验证'),
        ('reset_password', '密码重置'),
        ('change_email', '邮箱修改'),
    ], verbose_name="验证目的")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    is_used = models.BooleanField(default=False, verbose_name="是否已使用")

    def is_expired(self):
        """检查验证码是否过期（10分钟有效期）"""
        return timezone.now() > self.created_at + timedelta(minutes=10)

    def __str__(self):
        return f"{self.email} - {self.code} - {self.get_purpose_display()}"

    class Meta:
        verbose_name = "邮箱验证码"
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    @classmethod
    def generate_code(cls):
        """生成6位数字验证码"""
        return ''.join(random.choices(string.digits, k=6))



class UserCourse(models.Model):
    """课程列表模型"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='courses', verbose_name="用户", null=True, blank=True)
    video = models.ForeignKey(BiliVideo, on_delete=models.CASCADE, verbose_name="视频")
    custom_title = models.CharField(max_length=200, blank=True, null=True, verbose_name="自定义课程名称")
    add_time = models.DateTimeField(auto_now_add=True, verbose_name="添加时间")

    def __str__(self):
        return self.custom_title or f"{self.video.title}"

    class Meta:
        verbose_name = "课程"
        verbose_name_plural = verbose_name

class LearningProgress(models.Model):
    """学习进度模型"""
    user_course = models.ForeignKey(UserCourse, on_delete=models.CASCADE, related_name='progress', verbose_name="课程")
    episode = models.ForeignKey(VideoEpisode, on_delete=models.CASCADE, verbose_name="视频分集")
    is_completed = models.BooleanField(default=False, verbose_name="是否完成")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")
    update_time = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"{self.user_course} - {self.episode.title} - {'已完成' if self.is_completed else '未完成'}"

    class Meta:
        verbose_name = "学习进度"
        verbose_name_plural = verbose_name
        unique_together = ('user_course', 'episode')


class StudyPlan(models.Model):
    """学习计划模型"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='study_plans', verbose_name="用户", null=True, blank=True)
    user_course = models.OneToOneField(UserCourse, on_delete=models.CASCADE, related_name='study_plan', verbose_name="课程")
    total_days = models.IntegerField(verbose_name="计划学习总天数")
    daily_minutes = models.IntegerField(verbose_name="每天学习时间（分钟）")
    focus_modules = models.TextField(blank=True, verbose_name="重点学习模块")
    start_date = models.DateField(auto_now_add=True, verbose_name="开始日期")
    is_active = models.BooleanField(default=True, verbose_name="是否激活")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"{self.user_course} - {self.total_days}天计划"

    class Meta:
        verbose_name = "学习计划"
        verbose_name_plural = verbose_name

    @property
    def expected_end_date(self):
        """预期结束日期"""
        from datetime import timedelta
        return self.start_date + timedelta(days=self.total_days)

    @property
    def progress_percentage(self):
        """计划完成百分比"""
        total_episodes = self.user_course.video.episodes.count()
        completed_episodes = LearningProgress.objects.filter(
            user_course=self.user_course,
            is_completed=True
        ).count()
        return (completed_episodes / total_episodes * 100) if total_episodes > 0 else 0

    @property
    def days_passed(self):
        """已过天数"""
        from datetime import date
        return (date.today() - self.start_date).days + 1

    @property
    def is_overdue(self):
        """是否超期"""
        from datetime import date
        return date.today() > self.expected_end_date

    def get_total_completed_duration(self):
        """计算该课程已完成分集的总时长（分钟）"""
        completed_progress = LearningProgress.objects.filter(
            user_course=self.user_course,
            is_completed=True
        ).select_related('episode')

        total_duration = 0
        for progress in completed_progress:
            episode = progress.episode
            if episode.duration > 0:
                total_duration += episode.duration / 60  # 转换为分钟
            else:
                # 如果没有时长信息，使用默认值20分钟
                total_duration += 20
        return round(total_duration)


class DailyStudyRecord(models.Model):
    """每日学习记录"""
    study_plan = models.ForeignKey(StudyPlan, on_delete=models.CASCADE, related_name='daily_records', verbose_name="学习计划")
    study_date = models.DateField(verbose_name="学习日期")
    study_minutes = models.IntegerField(default=0, verbose_name="实际学习时间（分钟）")
    notes = models.TextField(blank=True, verbose_name="学习备忘/记录")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "每日学习记录"
        verbose_name_plural = verbose_name
        unique_together = ('study_plan', 'study_date')
        ordering = ['-study_date']

    def __str__(self):
        return f"{self.study_plan.user_course} - {self.study_date}"

    @property
    def episodes_count(self):
        """当天完成的分集数"""
        return self.completed_episodes.count()

    def get_daily_completed_episodes(self):
        """获取当日完成的分集列表（只显示当日勾选的分集）"""
        from django.utils import timezone
        from datetime import datetime, time

        # 获取当日的开始和结束时间
        study_date = self.study_date
        start_of_day = datetime.combine(study_date, time.min)
        end_of_day = datetime.combine(study_date, time.max)

        # 查找在当日完成的分集
        user_course = self.study_plan.user_course
        daily_progress = LearningProgress.objects.filter(
            user_course=user_course,
            is_completed=True,
            completed_at__range=(start_of_day, end_of_day)
        ).select_related('episode')

        return [progress.episode for progress in daily_progress]

    def get_daily_study_duration(self):
        """计算当日完成分集的总时长（分钟）"""
        episodes = self.get_daily_completed_episodes()
        total_duration = 0
        for episode in episodes:
            # 使用分集的实际时长（秒），转换为分钟
            if episode.duration > 0:
                total_duration += episode.duration / 60  # 转换为分钟
            else:
                # 如果没有时长信息，使用默认值20分钟
                total_duration += 20
        return round(total_duration)

    def get_total_progress(self):
        """获取总体学习进度"""
        user_course = self.study_plan.user_course
        total_episodes = user_course.video.episodes.count()
        completed_episodes = LearningProgress.objects.filter(
            user_course=user_course,
            is_completed=True
        ).count()
        return {
            'completed': completed_episodes,
            'total': total_episodes,
            'percentage': round((completed_episodes / total_episodes * 100), 1) if total_episodes > 0 else 0
        }

    def get_daily_episodes_detail(self):
        """获取当日完成分集的详细信息"""
        episodes = self.get_daily_completed_episodes()
        return [f"P{episode.order}: {episode.title}" for episode in episodes]

    def get_study_day_number(self):
        """获取这是学习计划的第几天"""
        plan_start_date = self.study_plan.start_date
        current_date = self.study_date
        return (current_date - plan_start_date).days + 1

    @property
    def episodes_count(self):
        """当天完成的分集数（重写，使用当日实际完成的分集）"""
        return len(self.get_daily_completed_episodes())


class ChatHistory(models.Model):
    """AI助手聊天历史记录模型"""
    session_id = models.CharField(max_length=100, verbose_name="会话ID")
    user_message = models.TextField(verbose_name="用户消息")
    ai_response = models.TextField(verbose_name="AI回复")
    chat_type = models.CharField(max_length=50, default='general', verbose_name="聊天类型")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    def __str__(self):
        return f"会话 {self.session_id} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        verbose_name = "聊天历史"
        verbose_name_plural = verbose_name
        ordering = ['-created_at']


class UserPreference(models.Model):
    """用户偏好设置模型"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preference', verbose_name="用户")

    # 学习提醒设置
    enable_learning_reminder = models.BooleanField(default=True, verbose_name="启用学习提醒")
    enable_content_filter_reminder = models.BooleanField(default=True, verbose_name="启用学习内容提醒")
    ignored_keywords = models.TextField(blank=True, verbose_name="忽略的关键词", help_text="JSON格式存储用户选择忽略的特定关键词")

    # 主题设置
    THEME_CHOICES = [
        ('light', '白天模式'),
        ('dark', '夜间模式'),
    ]
    theme_preference = models.CharField(max_length=10, choices=THEME_CHOICES, default='light', verbose_name="主题偏好")

    # 新手指南设置
    has_viewed_guide = models.BooleanField(default=False, verbose_name="已查看新手指南")

    # 其他设置可以在这里扩展
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"{self.user.username}的偏好设置"

    class Meta:
        verbose_name = "用户偏好设置"
        verbose_name_plural = verbose_name