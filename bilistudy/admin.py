from django.contrib import admin
from .models import BiliVideo, VideoEpisode, UserCourse, LearningProgress, EmailVerification, StudyPlan, DailyStudyRecord

@admin.register(BiliVideo)
class BiliVideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'bvid', 'author', 'pub_date', 'play_count', 'like_count')
    search_fields = ('title', 'bvid', 'author')
    list_filter = ('pub_date',)
    date_hierarchy = 'pub_date'

@admin.register(VideoEpisode)
class VideoEpisodeAdmin(admin.ModelAdmin):
    list_display = ('title', 'video', 'order', 'duration')
    list_filter = ('video',)
    search_fields = ('title',)
    ordering = ('video', 'order')

@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ('email', 'code', 'purpose', 'created_at', 'is_used', 'is_expired')
    list_filter = ('purpose', 'is_used', 'created_at')
    search_fields = ('email',)
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at',)


@admin.register(UserCourse)
class UserCourseAdmin(admin.ModelAdmin):
    list_display = ('user', 'video', 'custom_title', 'add_time')
    list_filter = ('add_time',)
    search_fields = ('user__username', 'video__title', 'custom_title')
    date_hierarchy = 'add_time'

@admin.register(StudyPlan)
class StudyPlanAdmin(admin.ModelAdmin):
    list_display = ('user', 'user_course', 'total_days', 'daily_minutes', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__username', 'user_course__video__title')
    date_hierarchy = 'created_at'

@admin.register(DailyStudyRecord)
class DailyStudyRecordAdmin(admin.ModelAdmin):
    list_display = ('study_plan', 'study_date', 'study_minutes', 'created_at')
    list_filter = ('study_date', 'created_at')
    search_fields = ('study_plan__user__username',)
    date_hierarchy = 'study_date'

@admin.register(LearningProgress)
class LearningProgressAdmin(admin.ModelAdmin):
    list_display = ('user_course', 'episode', 'is_completed', 'update_time')
    list_filter = ('is_completed', 'update_time')
    search_fields = ('episode__title',)
