from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('search/', views.search_videos, name='search_videos'),
    path('import/', views.import_video, name='import_video'),

    path('video/<str:bvid>/ajax/', views.video_detail_ajax, name='video_detail_ajax'),
    path('add_to_course/<str:bvid>/', views.add_to_course_list, name='add_to_course'),
    path('courses/', views.course_list, name='course_list'),
    path('course/<int:course_id>/', views.course_detail, name='course_detail'),
    path('update_progress/', views.update_progress, name='update_progress'),
    path('batch_update_progress/', views.batch_update_progress, name='batch_update_progress'),
    path('remove_course/<int:course_id>/', views.remove_from_course_list, name='remove_course'),
    path('update_course_title/<int:course_id>/', views.update_course_title, name='update_course_title'),
    path('test-api/', views.test_api, name='test_api'),

    # AI助手相关路由
    path('ai-assistant/', views.ai_assistant, name='ai_assistant'),
    path('ai-chat/', views.ai_chat, name='ai_chat'),
    path('get-chat-history/', views.get_chat_history, name='get_chat_history'),
    path('check-ai-status/', views.check_ai_status, name='check_ai_status'),
    path('update-learning-reminder/', views.update_learning_reminder_preference, name='update_learning_reminder'),
    path('update-content-filter/', views.update_content_filter_preference, name='update_content_filter'),
    path('update-theme/', views.update_theme_preference, name='update_theme'),
    path('ai-content-analysis/', views.ai_content_analysis, name='ai_content_analysis'),

    # 学习计划相关路由
    path('study-plans/', views.study_plans, name='study_plans'),
    path('create-study-plan/', views.create_study_plan, name='create_study_plan'),
    path('plan/<int:plan_id>/', views.plan_detail, name='plan_detail'),
    path('plan/<int:plan_id>/update-record/', views.update_daily_record, name='update_daily_record'),
    path('plan/<int:plan_id>/update-notes/', views.update_study_notes, name='update_study_notes'),
    path('plan/<int:plan_id>/delete-record/', views.delete_study_record, name='delete_study_record'),
    path('plan/<int:plan_id>/delete/', views.delete_study_plan, name='delete_study_plan'),
    path('plan/<int:plan_id>/export-pdf/', views.export_plan_pdf, name='export_plan_pdf'),

    # 用户认证相关路由
    path('auth/send-code/', views.send_verification_code, name='send_verification_code'),
    path('auth/register/', views.register_user, name='register_user'),
    path('auth/login/', views.login_user, name='login_user'),
    path('auth/logout/', views.logout_user, name='logout_user'),
    path('auth/reset-password-request/', views.reset_password_request, name='reset_password_request'),
    path('auth/reset-password-confirm/', views.reset_password_confirm, name='reset_password_confirm'),
    path('auth/check-username/', views.check_username_availability, name='check_username_availability'),
    path('auth/check-email/', views.check_email_availability, name='check_email_availability'),
    path('auth/account-settings/', views.account_settings, name='account_settings'),
    path('auth/change-password/', views.change_password, name='change_password'),
    path('auth/change-email/', views.change_email_request, name='change_email_request'),
    path('auth/delete-account/', views.delete_account, name='delete_account'),
    path('auth/change-username/', views.change_username, name='change_username'),
    path('beginner-guide/', views.beginner_guide, name='beginner_guide'),
    path('mark-guide-viewed/', views.mark_guide_viewed, name='mark_guide_viewed'),
]