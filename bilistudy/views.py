import json
import requests
import re
import os
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.utils.html import strip_tags
from django.template.loader import render_to_string
from bs4 import BeautifulSoup
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from .models import BiliVideo, VideoEpisode, UserCourse, LearningProgress, StudyPlan, DailyStudyRecord, EmailVerification, UserPreference
from .content_filter import analyze_search_content, need_ai_semantic_analysis, get_ai_analysis_prompt
# google.generativeai 将在需要时动态导入

def index(request):
    """首页视图"""
    # 检查用户是否已登录
    if not request.user.is_authenticated:
        # 检查是否有记住登录的cookie或session
        show_auth_modal = True
        # 如果用户明确选择了不显示登录框，则不显示
        if request.GET.get('no_auth') == '1':
            show_auth_modal = False
    else:
        show_auth_modal = False

    context = {
        'show_auth_modal': show_auth_modal
    }
    return render(request, 'bilistudy/index.html', context)

def search_videos(request):
    """搜索B站视频"""
    keyword = request.GET.get('keyword', '')
    sort_type = request.GET.get('sort', 'default')  # default, view, like
    page = int(request.GET.get('page', '1'))
    skip_warning = request.GET.get('skip_warning', 'false') == 'true'  # 是否跳过学习提醒



    if not keyword:
        return render(request, 'bilistudy/search_results.html', {'videos': []})

    # 检查是否需要学习提醒（仅在第一次搜索时检查）
    if not skip_warning and should_show_learning_reminder(request.user, keyword):
        return JsonResponse({
            'show_warning': True,
            'keyword': keyword,
            'sort_type': sort_type,
            'page': page
        })

    # 检查是否是BV号或视频链接
    extracted_bvid = extract_bvid_from_input(keyword)
    if extracted_bvid:
        # 如果是BV号或链接，直接获取该视频信息并显示详情页面
        try:
            # 检查数据库中是否已有该视频
            video = BiliVideo.objects.filter(bvid=extracted_bvid).first()

            if not video:
                # 如果数据库中没有，通过API获取
                api_url = "https://api.bilibili.com/x/web-interface/view"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': f'https://www.bilibili.com/video/{extracted_bvid}',
                }
                params = {'bvid': extracted_bvid}

                response = requests.get(api_url, params=params, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if data['code'] == 0:
                        video_data = data['data']
                        pub_date = datetime.fromtimestamp(video_data['pubdate']).date()

                        # 创建视频记录
                        video = BiliVideo.objects.create(
                            bvid=extracted_bvid,
                            title=video_data['title'],
                            cover=video_data['pic'],
                            author=video_data['owner']['name'],
                            pub_date=pub_date,
                            play_count=video_data['stat']['view'],
                            like_count=video_data['stat']['like'],
                            description=video_data['desc']
                        )

                        # 创建分集信息
                        if video_data.get('videos', 0) > 1:
                            for i, page_info in enumerate(video_data.get('pages', [])):
                                VideoEpisode.objects.create(
                                    video=video,
                                    cid=page_info['cid'],
                                    title=page_info['part'],
                                    duration=page_info['duration'],
                                    order=i+1
                                )
                        else:
                            VideoEpisode.objects.create(
                                video=video,
                                cid=video_data.get('cid', 'cid1'),
                                title="完整视频",
                                duration=video_data.get('duration', 600),
                                order=1
                            )

            if video:
                # 检查视频是否已在课程列表中
                is_in_course_list = UserCourse.objects.filter(video=video).exists()

                # 对单个视频进行内容检测
                content_analysis = None
                should_analyze = True

                if request.user.is_authenticated:
                    try:
                        user_preference = UserPreference.objects.get(user=request.user)
                        should_analyze = user_preference.enable_content_filter_reminder
                    except UserPreference.DoesNotExist:
                        should_analyze = True  # 默认启用检测

                if should_analyze:
                    video_data = [{
                        'title': video.title,
                        'author': video.author,
                        'desc': video.description,
                        'zone': getattr(video, 'zone', ''),
                        'tname': getattr(video, 'tname', '')
                    }]
                    content_analysis = analyze_search_content(keyword, video_data)

                # 获取视频分集信息
                episodes = VideoEpisode.objects.filter(video=video).order_by('order')

                # 返回单视频详情页面
                return render(request, 'bilistudy/search_results.html', {
                    'single_video': video,
                    'episodes': episodes,
                    'is_in_course_list': is_in_course_list,
                    'keyword': keyword,
                    'sort_type': sort_type,
                    'is_single_video': True,
                    'content_analysis': content_analysis
                })
        except Exception as e:
            print(f"获取单个视频信息失败: {e}")
            messages.error(request, f"获取视频信息失败: {str(e)}")
            return render(request, 'bilistudy/search_results.html', {'videos': [], 'keyword': keyword, 'sort_type': sort_type})
    
    # B站搜索API
    api_url = "https://api.bilibili.com/x/web-interface/search/type"
    params = {
        'search_type': 'video',
        'keyword': keyword,
        'page': page,
        'order': 'totalrank'  # 默认综合排序
    }
    
    # 根据排序类型设置参数
    if sort_type == 'view':
        params['order'] = 'click'  # 播放量排序
    elif sort_type == 'like':
        params['order'] = 'stow'  # 点赞数排序（收藏数，通常与点赞相关）
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://www.bilibili.com',
        'Cookie': 'buvid3=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc'
    }
    
    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=5)  # 减少超时时间
        
        # 检查响应状态码
        if response.status_code != 200:
            messages.error(request, f"搜索请求失败: HTTP {response.status_code}")
            return render(request, 'bilistudy/search_results.html', {'videos': [], 'keyword': keyword, 'sort_type': sort_type, 'is_single_video': False})
        
        # 尝试解析JSON响应
        try:
            data = response.json()
        except json.JSONDecodeError:
            # 如果JSON解析失败，打印响应内容以便调试
            print(f"JSON解析失败，响应内容: {response.text[:200]}...")
            messages.error(request, "搜索结果格式错误，无法解析")
            return render(request, 'bilistudy/search_results.html', {'videos': [], 'keyword': keyword, 'sort_type': sort_type})
        
        # 检查API返回结果
        if 'code' not in data or data['code'] != 0 or 'data' not in data or 'result' not in data.get('data', {}):
            # 如果API返回错误或数据格式不符合预期，返回空结果
            print(f"搜索失败，API返回: {data.get('code')}, {data.get('message', '')}")

            # 即使搜索失败也进行内容分析（仅基于搜索关键词）
            content_analysis = None
            should_analyze = True  # 默认进行检测

            # 只有登录用户且明确关闭了内容提醒才不检测
            if request.user.is_authenticated:
                try:
                    user_preference = UserPreference.objects.get(user=request.user)
                    # 检查字段是否存在且为False
                    if hasattr(user_preference, 'enable_content_filter_reminder'):
                        should_analyze = user_preference.enable_content_filter_reminder
                    else:
                        # 字段不存在，默认启用
                        should_analyze = True
                except UserPreference.DoesNotExist:
                    # 用户偏好不存在，默认启用检测
                    should_analyze = True

            if should_analyze:
                content_analysis = analyze_search_content(keyword, [])  # 空视频列表，仅分析搜索关键词

            return render(request, 'bilistudy/search_results.html', {
                'videos': [],
                'videos_json': '[]',
                'keyword': keyword,
                'sort_type': sort_type,
                'page': 1,
                'total_pages': 1,
                'page_range': [1],
                'is_single_video': False,
                'content_analysis': content_analysis,
                'search_failed': True
            })
        else:
            videos = data['data']['result']
            total_pages = data['data'].get('numPages', 1)

            # 客户端排序（确保排序正确）
            def safe_int(value):
                """安全地将值转换为整数"""
                try:
                    if isinstance(value, (int, float)):
                        return int(value)
                    elif isinstance(value, str):
                        # 移除逗号和其他非数字字符
                        clean_value = ''.join(filter(str.isdigit, value))
                        return int(clean_value) if clean_value else 0
                    else:
                        return 0
                except (ValueError, TypeError):
                    return 0

            if sort_type == 'like' and videos:
                # 按点赞数降序排序
                videos = sorted(videos, key=lambda x: safe_int(x.get('like', 0)), reverse=True)
            elif sort_type == 'view' and videos:
                # 按播放量降序排序
                videos = sorted(videos, key=lambda x: safe_int(x.get('play', 0)), reverse=True)


            
            # 清理视频标题和作者名称中的HTML标签，并过滤未知时长的视频
            filtered_videos = []
            for video in videos:
                if 'title' in video:
                    # 去除HTML标签
                    video['title'] = strip_tags(video['title'])
                    # 替换特殊的em标签
                    video['title'] = re.sub(r'<em class="keyword">|</em>', '', video['title'])

                if 'author' in video:
                    video['author'] = strip_tags(video['author'])

                # 过滤掉未知时长的视频
                duration = video.get('duration', '')
                if duration and duration != '0:00' and duration != '--:--':
                    filtered_videos.append(video)

            videos = filtered_videos

            # 智能内容检测 - 改为异步处理，不阻塞搜索结果显示
            content_analysis = None
            should_analyze = True  # 默认进行检测

            # 只有登录用户且明确关闭了内容提醒才不检测
            if request.user.is_authenticated:
                try:
                    user_preference = UserPreference.objects.get(user=request.user)
                    # 检查字段是否存在且为False
                    if hasattr(user_preference, 'enable_content_filter_reminder'):
                        should_analyze = user_preference.enable_content_filter_reminder
                    else:
                        # 字段不存在，默认启用
                        should_analyze = True
                except UserPreference.DoesNotExist:
                    # 用户偏好不存在，默认启用检测
                    should_analyze = True

            # 快速内容检测，不进行深度AI分析
            if should_analyze:
                try:
                    # 使用简化的内容分析，避免AI调用
                    content_analysis = analyze_search_content(keyword, videos[:3])  # 只分析前3个视频
                except:
                    # 如果分析失败，不影响搜索结果显示
                    content_analysis = None

            # 计算分页范围，最多显示5个页码
            if total_pages <= 5:
                page_range = range(1, total_pages + 1)
            else:
                if page <= 3:
                    page_range = range(1, 6)
                elif page > total_pages - 3:
                    page_range = range(total_pages - 4, total_pages + 1)
                else:
                    page_range = range(page - 2, page + 3)

            # 为JavaScript准备视频数据
            import json
            videos_json = json.dumps(videos[:3] if videos else [])  # 只传递前3个视频用于AI分析

            return render(request, 'bilistudy/search_results.html', {
                'videos': videos,
                'videos_json': videos_json,
                'keyword': keyword,
                'sort_type': sort_type,
                'page': page,
                'total_pages': total_pages,
                'page_range': page_range,
                'is_single_video': False,
                'content_analysis': content_analysis
            })
    
    except requests.RequestException as e:
        messages.error(request, f"搜索请求出错: {str(e)}")
        return render(request, 'bilistudy/search_results.html', {'videos': [], 'keyword': keyword, 'sort_type': sort_type})
    except Exception as e:
        messages.error(request, f"搜索出错: {str(e)}")
        return render(request, 'bilistudy/search_results.html', {'videos': [], 'keyword': keyword, 'sort_type': sort_type})


def is_non_learning_content(keyword):
    """检测搜索内容是否可能与学习无关"""
    # 非学习相关的关键词列表
    non_learning_keywords = [
        # 娱乐类
        '搞笑', '段子', '沙雕', '鬼畜', '整活', '梗', '表情包', '恶搞', '搞怪', '逗比',
        '娱乐', '综艺', '脱口秀', '相声', '小品', '喜剧', '搞笑视频', '爆笑',

        # 游戏类
        '游戏', '王者荣耀', '英雄联盟', 'LOL', '吃鸡', '原神', '和平精英', '我的世界',
        '游戏解说', '游戏攻略', '游戏直播', '电竞', '主机游戏', '手游', '网游',
        '明日方舟','csgo','第五人格',

        # 生活娱乐类
        '美食', '吃播', '探店', '旅游', '旅行', 'vlog', '日常', '生活',
        '化妆', '护肤', '穿搭', '时尚', '美妆', '发型', '减肥', '健身',

        # 八卦娱乐类
        '明星', '娱乐圈', '八卦', '绯闻', '爆料', '瓜', '热搜',
        '网红', '主播', '直播', '带货', '种草',

        # 音乐娱乐类
        '音乐', '歌曲', '翻唱', '舞蹈', '跳舞', 'MV', '演唱会', '音乐节',

        # 影视娱乐类
        '电影', '电视剧', '动漫', '番剧', '追剧', '影评', '剧评',

        # 其他娱乐类
        '宠物', '萌宠', '猫', '狗', '可爱', '萌', '治愈',
        '聊天', '闲聊', '摸鱼', '划水', '无聊','小姐姐','美女'
    ]

    # 学习相关的关键词（如果包含这些，则认为是学习内容）
    learning_keywords = [
        '教程', '学习', '课程', '教学', '培训', '讲解', '入门', '基础', '进阶', '高级',
        '编程', '代码', '开发', '算法', '数据结构', '计算机', '软件', '技术',
        '数学', '物理', '化学', '生物', '历史', '地理', '语文', '英语', '外语',
        '考试', '考研', '高考', '四六级', '托福', '雅思', '公务员', '证书',
        '技能', '知识', '科学', '研究', '学术', '论文', '实验', '理论',
        '工具', '软件教程', '操作', '使用方法', '技巧', '方法', '原理',
        '专业', '行业', '职业', '工作', '面试', '求职', '简历','机器学习','系统','深度学习','统计',


    ]

    keyword_lower = keyword.lower()

    # 如果包含学习相关关键词，认为是学习内容
    for learning_word in learning_keywords:
        if learning_word in keyword_lower:
            return False

    # 如果包含非学习关键词，认为可能不是学习内容
    for non_learning_word in non_learning_keywords:
        if non_learning_word in keyword_lower:
            return True

    # 默认认为是学习内容（给用户更多自由度）
    return False


def should_show_learning_reminder(user, keyword):
    """检查是否应该显示学习提醒"""
    # 如果用户未登录，不显示提醒
    if not user.is_authenticated:
        return False

    # 获取用户偏好设置
    try:
        preference = user.preference
    except UserPreference.DoesNotExist:
        # 如果没有偏好设置，创建默认设置
        preference = UserPreference.objects.create(user=user)

    # 如果用户关闭了学习提醒，不显示
    if not preference.enable_learning_reminder:
        return False

    # 检查是否是用户忽略的特定关键词
    import json
    try:
        ignored_keywords = json.loads(preference.ignored_keywords) if preference.ignored_keywords else []
        if keyword.lower() in [k.lower() for k in ignored_keywords]:
            return False
    except (json.JSONDecodeError, TypeError):
        pass

    # 使用原有的内容检测逻辑
    return is_non_learning_content(keyword)


def extract_bvid_from_input(video_input):
    """统一的BV号提取函数 - 保持原始大小写，支持复杂URL参数"""
    if not video_input or not isinstance(video_input, str):
        return None

    video_input = video_input.strip()

    # 1. 直接输入的完整BV号 - 保持原始大小写
    direct_match = re.match(r'^(BV[A-Za-z0-9]{10})$', video_input, re.IGNORECASE)
    if direct_match:
        return direct_match.group(1)  # 不转换大小写

    # 2. 从URL中提取BV号 - 支持各种参数和格式
    url_patterns = [
        # 标准B站视频链接，支持后面的参数
        r'bilibili\.com/video/(BV[A-Za-z0-9]{10})(?:[/?]|$)',
        # 短链接格式
        r'/video/(BV[A-Za-z0-9]{10})(?:[/?]|$)',
        # 查询参数中的BV号
        r'[?&]bv=(BV[A-Za-z0-9]{10})',
        r'[?&]BV=(BV[A-Za-z0-9]{10})',
        # 移动端链接
        r'm\.bilibili\.com/video/(BV[A-Za-z0-9]{10})',
        # 其他可能的格式
        r'b23\.tv.*/(BV[A-Za-z0-9]{10})'
    ]

    for pattern in url_patterns:
        url_match = re.search(pattern, video_input, re.IGNORECASE)
        if url_match:
            return url_match.group(1)  # 保持原始大小写

    # 3. 从任意文本中提取标准BV号 - 保持原始大小写
    text_match = re.search(r'BV[A-Za-z0-9]{10}', video_input)
    if text_match:
        return text_match.group(0)  # 保持原始大小写

    # 4. 如果以BV开头但格式不完整，返回None（不进行补全）
    if re.match(r'^BV', video_input, re.IGNORECASE):
        return None  # 格式不完整的BV号直接返回None

    return None

def import_video(request):
    """直接导入B站视频"""
    if request.method == 'POST':
        video_input = request.POST.get('video_input', '').strip()
        print(f"原始输入: {video_input}")

        # 使用统一的BV号提取函数
        bvid = extract_bvid_from_input(video_input)

        if not bvid:
            messages.error(request, "无效的BV号或视频链接，请重新输入")
            return redirect('index')

        print(f"提取的BV号: {bvid}")

        # 导入成功，返回首页
        messages.success(request, f"视频导入成功！BV号: {bvid}")
        return redirect('index')

    return redirect('index')



def video_detail_ajax(request, bvid):
    """视频详情AJAX请求处理 - 使用网页爬虫获取视频信息"""
    try:
        print(f"开始处理视频详情请求: {bvid}")

        # 验证BV号格式
        if not bvid or not re.match(r'^BV[A-Za-z0-9]{10}$', bvid):
            return JsonResponse({
                'success': False,
                'message': f'无效的BV号格式: {bvid}'
            })

        # 检查数据库中是否已有该视频
        video = BiliVideo.objects.filter(bvid=bvid).first()

        if not video:
            print(f"数据库中未找到视频 {bvid}，开始获取视频信息")

            # 首先尝试使用API获取视频信息
            api_success = False
            api_error_msg = ""

            try:
                api_url = "https://api.bilibili.com/x/web-interface/view"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Referer': f'https://www.bilibili.com/video/{bvid}',
                    'Origin': 'https://www.bilibili.com',
                    'Cookie': 'buvid3=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc',
                    'Accept': 'application/json, text/plain, */*',
                    'Connection': 'keep-alive'
                }
                params = {
                    'bvid': bvid
                }

                print(f"尝试通过API获取视频信息: {bvid}")
                response = requests.get(api_url, params=params, headers=headers, timeout=15)
                print(f"API响应状态码: {response.status_code}")

                if response.status_code == 200:
                    try:
                        data = response.json()
                        print(f"API返回码: {data.get('code')}, 消息: {data.get('message', 'N/A')}")

                        if data['code'] == 0:
                            video_data = data['data']
                            pub_date = datetime.fromtimestamp(video_data['pubdate']).date()

                            # 创建视频记录
                            video = BiliVideo.objects.create(
                                bvid=bvid,
                                title=video_data['title'],
                                cover=video_data['pic'],
                                author=video_data['owner']['name'],
                                pub_date=pub_date,
                                play_count=video_data['stat']['view'],
                                like_count=video_data['stat']['like'],
                                description=video_data['desc']
                            )

                            # 获取分集信息
                            if video_data.get('videos', 0) > 1:
                                # 获取分P列表
                                for i, page in enumerate(video_data.get('pages', [])):
                                    VideoEpisode.objects.create(
                                        video=video,
                                        cid=page['cid'],
                                        title=page['part'],
                                        duration=page['duration'],
                                        order=i+1
                                    )
                            else:
                                # 如果是单P视频，创建一个默认分集
                                VideoEpisode.objects.create(
                                    video=video,
                                    cid=video_data.get('cid', 'cid1'),
                                    title="完整视频",
                                    duration=video_data.get('duration', 600),
                                    order=1
                                )

                            api_success = True
                            print("API获取视频信息成功")
                        else:
                            # 处理特定的API错误码
                            error_code = data.get('code')
                            error_message = data.get('message', '未知错误')

                            if error_code == -400:
                                api_error_msg = f"视频不存在或BV号无效 (错误码: {error_code})"
                            elif error_code == -403:
                                api_error_msg = f"视频已被删除或设为私密 (错误码: {error_code})"
                            elif error_code == -404:
                                api_error_msg = f"视频不存在 (错误码: {error_code})"
                            else:
                                api_error_msg = f"API返回错误: {error_code} - {error_message}"

                            print(api_error_msg)
                    except json.JSONDecodeError as e:
                        api_error_msg = f"API响应JSON解析失败: {str(e)}"
                        print(api_error_msg)
                else:
                    api_error_msg = f"API请求失败: HTTP {response.status_code}"
                    print(api_error_msg)

            except requests.RequestException as e:
                api_error_msg = f"API请求异常: {str(e)}"
                print(api_error_msg)
            except Exception as e:
                api_error_msg = f"API处理异常: {str(e)}"
                print(api_error_msg)
            
            # 如果API获取失败，尝试使用网页爬虫获取视频信息
            if not api_success:
                crawler_success = False
                crawler_error_msg = ""

                try:
                    print(f"API获取失败({api_error_msg})，尝试通过网页爬虫获取视频信息: {bvid}")
                    # 构建B站视频页面URL
                    video_url = f"https://www.bilibili.com/video/{bvid}"
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                        'Referer': 'https://www.bilibili.com',
                        'Cookie': 'buvid3=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                        'Cache-Control': 'no-cache'
                    }

                    response = requests.get(video_url, headers=headers, timeout=15)
                    print(f"网页请求状态码: {response.status_code}")

                    if response.status_code == 200:
                        # 使用BeautifulSoup解析HTML
                        soup = BeautifulSoup(response.text, 'html.parser')

                        # 检查页面是否包含视频内容
                        if '视频不存在' in response.text or '稿件不可见' in response.text:
                            crawler_error_msg = "视频不存在或已被删除"
                            raise Exception(crawler_error_msg)

                        # 提取视频标题 - 尝试多种选择器
                        title = f"视频 {bvid}"  # 默认标题
                        title_selectors = [
                            'h1.video-title',
                            'h1[data-title]',
                            '.video-title',
                            'title'
                        ]

                        for selector in title_selectors:
                            title_tag = soup.select_one(selector)
                            if title_tag:
                                if selector == 'title':
                                    title_text = title_tag.text.strip()
                                    if '_哔哩哔哩_bilibili' in title_text:
                                        title = title_text.replace('_哔哩哔哩_bilibili', '').strip()
                                else:
                                    title = title_tag.get('title') or title_tag.text.strip()
                                if title and title != f"视频 {bvid}":
                                    break

                        print(f"提取的标题: {title}")

                        # 提取UP主信息 - 尝试多种选择器
                        author = "未知UP主"
                        author_selectors = [
                            'a.up-name',
                            '.up-name',
                            '.username',
                            '.up-detail .name'
                        ]

                        for selector in author_selectors:
                            author_tag = soup.select_one(selector)
                            if author_tag:
                                author = author_tag.text.strip()
                                if author:
                                    break

                        print(f"提取的UP主: {author}")

                        # 从页面中提取JSON数据
                        cover_url = "https://i0.hdslb.com/bfs/archive/1234567890abcdef.jpg"  # 默认封面
                        description = ""
                        episode_list = []

                        script_tags = soup.find_all('script')
                        for script in script_tags:
                            if script.string and 'window.__INITIAL_STATE__' in script.string:
                                match = re.search(r'window\.__INITIAL_STATE__=(.+?);\(function', script.string)
                                if match:
                                    try:
                                        initial_state = json.loads(match.group(1))
                                        if 'videoData' in initial_state:
                                            video_data = initial_state['videoData']

                                            # 提取封面
                                            if 'pic' in video_data:
                                                cover_url = video_data['pic']

                                            # 提取描述
                                            if 'desc' in video_data:
                                                description = video_data['desc']

                                            # 提取分集信息
                                            if 'pages' in video_data:
                                                pages = video_data['pages']
                                                for i, page in enumerate(pages):
                                                    episode_list.append({
                                                        'cid': page.get('cid', f"cid{i+1}"),
                                                        'title': page.get('part', f"第{i+1}集"),
                                                        'duration': page.get('duration', 600),
                                                        'order': i+1
                                                    })
                                        break
                                    except json.JSONDecodeError as e:
                                        print(f"解析JSON数据失败: {str(e)}")
                                        continue

                        # 如果没有找到分集信息，创建一个默认分集
                        if not episode_list:
                            episode_list.append({
                                'cid': "cid1",
                                'title': "完整视频",
                                'duration': 600,  # 默认10分钟
                                'order': 1
                            })

                        # 创建视频记录
                        video = BiliVideo.objects.create(
                            bvid=bvid,
                            title=title,
                            cover=cover_url,
                            author=author,
                            pub_date=datetime.now().date(),
                            play_count=0,
                            like_count=0,
                            description=description
                        )

                        # 创建分集记录
                        for episode in episode_list:
                            VideoEpisode.objects.create(
                                video=video,
                                cid=episode['cid'],
                                title=episode['title'],
                                duration=episode['duration'],
                                order=episode['order']
                            )

                        crawler_success = True
                        print("网页爬虫获取视频信息成功")
                    else:
                        crawler_error_msg = f"网页请求失败: HTTP {response.status_code}"
                        raise Exception(crawler_error_msg)

                except requests.RequestException as e:
                    crawler_error_msg = f"网页请求异常: {str(e)}"
                    print(crawler_error_msg)
                except Exception as e:
                    crawler_error_msg = f"网页爬虫处理异常: {str(e)}"
                    print(crawler_error_msg)

                # 如果爬虫也失败，返回详细错误信息
                if not crawler_success:
                    error_details = f"获取视频信息失败。API错误: {api_error_msg}; 爬虫错误: {crawler_error_msg}"
                    print(error_details)
                    return JsonResponse({
                        'success': False,
                        'message': error_details
                    })
        else:
            print(f"数据库中找到视频: {video.title}")

        # 检查视频是否已在课程列表中
        is_in_course_list = UserCourse.objects.filter(video=video).exists()

        # 获取视频分集信息
        episodes = VideoEpisode.objects.filter(video=video).order_by('order')

        # 渲染视频详情HTML
        html = render_to_string('bilistudy/video_detail_modal.html', {
            'video': video,
            'episodes': episodes,
            'is_in_course_list': is_in_course_list
        })

        print(f"成功获取视频信息: {video.title}")
        print(f"HTML长度: {len(html)}")
        return JsonResponse({
            'success': True,
            'html': html,
            'title': video.title,
            'is_in_course_list': is_in_course_list
        })

    except Exception as e:
        import traceback
        error_msg = f"获取视频信息出错: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': error_msg
        })

@require_POST
@login_required
def add_to_course_list(request, bvid):
    """添加视频到课程列表"""
    video = get_object_or_404(BiliVideo, bvid=bvid)

    # 获取自定义课程名称
    custom_title = request.POST.get('custom_title', '').strip()
    course_title = custom_title if custom_title else video.title

    # 检查是否已在课程列表中
    user_course, created = UserCourse.objects.get_or_create(
        user=request.user,
        video=video,
        defaults={'custom_title': course_title}
    )

    if created:
        # 为每个分集创建学习进度记录
        for episode in video.episodes.all():
            LearningProgress.objects.create(
                user_course=user_course,
                episode=episode
            )

        # 计算视频总时长
        total_duration = sum(episode.duration for episode in video.episodes.all())
        total_episodes = video.episodes.count()

        # 返回JSON响应，包含课程信息用于学习计划弹窗
        return JsonResponse({
            'success': True,
            'created': True,
            'message': f"《{course_title}》已添加到您的课程列表",
            'course_id': user_course.id,
            'course_title': course_title,
            'total_episodes': total_episodes,
            'total_duration': total_duration,
            'total_duration_formatted': f"{total_duration // 3600}小时{(total_duration % 3600) // 60}分钟"
        })
    else:
        # 更新自定义标题
        if custom_title:
            user_course.custom_title = course_title
            user_course.save()
            return JsonResponse({
                'success': True,
                'created': False,
                'message': f"《{course_title}》标题已更新"
            })
        else:
            return JsonResponse({
                'success': True,
                'created': False,
                'message': f"《{video.title}》已在课程列表中"
            })

@login_required
def course_list(request):
    """用户课程列表"""
    courses = UserCourse.objects.filter(user=request.user).order_by('-add_time')

    # 为每个课程计算学习进度
    for course in courses:
        progress_records = LearningProgress.objects.filter(user_course=course)
        total_episodes = progress_records.count()
        completed_episodes = progress_records.filter(is_completed=True).count()

        if total_episodes > 0:
            course.progress_percentage = round((completed_episodes / total_episodes) * 100, 1)
        else:
            course.progress_percentage = 0

    return render(request, 'bilistudy/course_list.html', {'courses': courses})

@login_required
def course_detail(request, course_id):
    """课程详情和学习进度"""
    course = get_object_or_404(UserCourse, id=course_id, user=request.user)
    progress = LearningProgress.objects.filter(user_course=course).order_by('episode__order')

    # 计算总体进度
    total_episodes = progress.count()
    completed_episodes = progress.filter(is_completed=True).count()
    completion_rate = int(completed_episodes / total_episodes * 100) if total_episodes > 0 else 0

    # 获取该课程的学习计划（如果存在）
    try:
        study_plan = StudyPlan.objects.get(user_course=course, user=request.user)
    except StudyPlan.DoesNotExist:
        study_plan = None

    return render(request, 'bilistudy/course_detail.html', {
        'course': course,
        'progress': progress,
        'completion_rate': completion_rate,
        'study_plan': study_plan
    })

@require_POST
@login_required
def update_progress(request):
    """更新学习进度"""
    progress_id = request.POST.get('progress_id')
    is_completed = request.POST.get('is_completed') == 'true'

    progress = get_object_or_404(LearningProgress, id=progress_id)
    # 验证用户权限
    if progress.user_course.user != request.user:
        return JsonResponse({'success': False, 'message': '无权限操作'})

    # 检查状态是否真的发生了变化
    old_is_completed = progress.is_completed

    # 更新完成状态和时间
    progress.is_completed = is_completed
    if is_completed:
        # 只有当分集从未完成变为完成时，才更新完成时间
        if not old_is_completed:
            from django.utils import timezone
            progress.completed_at = timezone.now()
        # 如果之前已经完成，保持原有的完成时间不变
    else:
        # 取消完成时，清空完成时间
        progress.completed_at = None
    progress.save()

    # 同步到学习计划
    sync_progress_to_study_plan(progress, is_completed)

    # 计算更新后的进度百分比
    user_course = progress.user_course
    total_progress = LearningProgress.objects.filter(user_course=user_course).count()
    completed_progress = LearningProgress.objects.filter(user_course=user_course, is_completed=True).count()
    completion_rate = (completed_progress / total_progress * 100) if total_progress > 0 else 0

    return JsonResponse({
        'status': 'success',
        'completion_rate': round(completion_rate, 1),
        'completed_count': completed_progress,
        'total_count': total_progress
    })


def sync_progress_to_study_plan(progress, is_completed):
    """同步学习进度到学习计划"""
    try:
        # 检查是否有学习计划
        if hasattr(progress.user_course, 'study_plan'):
            study_plan = progress.user_course.study_plan

            # 如果是完成分集，使用分集的完成时间来确定学习日期
            if is_completed and progress.completed_at:
                study_date = progress.completed_at.date()
            else:
                # 如果是取消完成或没有完成时间，使用今天
                from datetime import date
                study_date = date.today()

            # 获取或创建对应日期的学习记录
            daily_record, created = DailyStudyRecord.objects.get_or_create(
                study_plan=study_plan,
                study_date=study_date,
                defaults={'study_minutes': 0, 'notes': ''}
            )

            # 如果是新创建的学习记录，设置默认学习时间
            if created:
                daily_record.study_minutes = study_plan.daily_minutes
                daily_record.save()

            # 如果是取消完成分集，需要检查该日期是否还有其他完成的分集
            if not is_completed:
                # 检查该日期是否还有其他完成的分集
                from django.utils import timezone
                from datetime import datetime, time

                start_of_day = datetime.combine(study_date, time.min)
                end_of_day = datetime.combine(study_date, time.max)

                remaining_episodes = LearningProgress.objects.filter(
                    user_course=progress.user_course,
                    is_completed=True,
                    completed_at__range=(start_of_day, end_of_day)
                ).exclude(id=progress.id).count()

                # 如果该日期没有其他完成的分集，删除该日的学习记录
                if remaining_episodes == 0:
                    daily_record.delete()

    except Exception as e:
        # 记录错误但不影响主流程
        print(f"同步学习计划失败: {str(e)}")


@require_POST
@login_required
def batch_update_progress(request):
    """批量更新学习进度"""
    progress_ids = request.POST.getlist('progress_ids[]')
    is_completed = request.POST.get('is_completed') == 'true'

    user_course = None
    for progress_id in progress_ids:
        progress = get_object_or_404(LearningProgress, id=progress_id)
        # 验证用户权限
        if progress.user_course.user != request.user:
            continue

        # 检查状态是否真的发生了变化
        old_is_completed = progress.is_completed

        # 更新完成状态和时间
        progress.is_completed = is_completed
        if is_completed:
            # 只有当分集从未完成变为完成时，才更新完成时间
            if not old_is_completed:
                from django.utils import timezone
                progress.completed_at = timezone.now()
            # 如果之前已经完成，保持原有的完成时间不变
        else:
            # 取消完成时，清空完成时间
            progress.completed_at = None
        progress.save()

        # 同步到学习计划
        sync_progress_to_study_plan(progress, is_completed)

        # 记录用户课程用于计算进度
        if user_course is None:
            user_course = progress.user_course

    # 计算更新后的进度百分比
    if user_course:
        total_progress = LearningProgress.objects.filter(user_course=user_course).count()
        completed_progress = LearningProgress.objects.filter(user_course=user_course, is_completed=True).count()
        completion_rate = (completed_progress / total_progress * 100) if total_progress > 0 else 0

        return JsonResponse({
            'status': 'success',
            'completion_rate': round(completion_rate, 1),
            'completed_count': completed_progress,
            'total_count': total_progress
        })

    return JsonResponse({'status': 'success'})

@require_POST
@login_required
def remove_from_course_list(request, course_id):
    """从课程列表中移除课程"""
    course = get_object_or_404(UserCourse, id=course_id, user=request.user)
    video_title = course.video.title
    course.delete()

    messages.success(request, f"《{video_title}》已从课程列表中移除")
    return redirect('course_list')

@require_POST
@login_required
def update_course_title(request, course_id):
    """更新课程标题"""
    course = get_object_or_404(UserCourse, id=course_id, user=request.user)

    # 获取新的课程标题
    new_title = request.POST.get('custom_title', '').strip()

    # 验证标题
    if not new_title:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': '课程名称不能为空'})
        messages.error(request, "课程名称不能为空")
        return redirect('course_list')

    if len(new_title) > 100:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': '课程名称不能超过100个字符'})
        messages.error(request, "课程名称不能超过100个字符")
        return redirect('course_list')

    # 保存原标题用于消息显示
    old_title = course.custom_title or course.video.title

    # 更新课程标题
    course.custom_title = new_title
    course.save()

    # 根据请求类型返回不同响应
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'message': f'课程名称已从《{old_title}》更新为《{new_title}》',
            'new_title': new_title,
            'old_title': old_title
        })

    messages.success(request, f"课程名称已从《{old_title}》更新为《{new_title}》")
    return redirect('course_list')

def test_api(request):
    """测试B站API连接"""
    bvid = request.GET.get('bvid', 'BV1xx411c7mD')  # 默认使用一个示例BV号
    
    # 测试结果
    result = {
        'bvid': bvid,
        'api1': {},
        'api2': {}
    }
    
    # 测试API1 - 详细接口
    api_url1 = "https://api.bilibili.com/x/web-interface/view/detail"
    headers1 = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://www.bilibili.com/video/' + bvid,
        'Origin': 'https://www.bilibili.com',
        'Cookie': 'buvid3=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc'
    }
    params1 = {
        'bvid': bvid
    }
    
    try:
        response1 = requests.get(api_url1, params=params1, headers=headers1, timeout=15)
        
        result['api1'] = {
            'status_code': response1.status_code,
            'content_type': response1.headers.get('Content-Type', ''),
            'encoding': response1.encoding
        }
        
        try:
            result['api1']['data'] = response1.json()
        except ValueError:
            result['api1']['text'] = response1.text[:500]
    except Exception as e:
        result['api1']['error'] = str(e)
    
    # 测试API2 - 基础接口
    api_url2 = "https://api.bilibili.com/x/web-interface/view"
    headers2 = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://www.bilibili.com',
        'Cookie': 'buvid3=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc'
    }
    params2 = {
        'bvid': bvid
    }
    
    try:
        response2 = requests.get(api_url2, params=params2, headers=headers2, timeout=15)
        
        result['api2'] = {
            'status_code': response2.status_code,
            'content_type': response2.headers.get('Content-Type', ''),
            'encoding': response2.encoding
        }
        
        try:
            result['api2']['data'] = response2.json()
        except ValueError:
            result['api2']['text'] = response2.text[:500]
    except Exception as e:
        result['api2']['error'] = str(e)

    return JsonResponse(result)


# AI助手相关视图函数
def ai_assistant(request):
    """AI助手主页面"""
    if request.user.is_authenticated:
        # 获取当前用户的课程列表
        user_courses = UserCourse.objects.filter(user=request.user).order_by('-add_time')[:10]

        # 获取当前用户最近的学习进度
        recent_progress = LearningProgress.objects.filter(
            user_course__user=request.user,
            is_completed=True
        ).order_by('-update_time')[:5]

        # 计算总学习时长（从学习计划的每日记录中）
        total_study_minutes = 0
        study_plans = StudyPlan.objects.filter(user=request.user)
        for plan in study_plans:
            total_study_minutes += sum(
                record.study_minutes for record in plan.daily_records.all()
            )
    else:
        # 未登录用户显示空数据
        user_courses = []
        recent_progress = []
        total_study_minutes = 0

    return render(request, 'bilistudy/ai_assistant.html', {
        'user_courses': user_courses,
        'recent_progress': recent_progress,
        'total_study_minutes': total_study_minutes
    })



@require_POST
def update_learning_reminder_preference(request):
    """更新学习提醒偏好设置"""
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': '请先登录'})

    try:
        # 获取或创建用户偏好设置
        preference, created = UserPreference.objects.get_or_create(user=request.user)

        action = request.POST.get('action')
        keyword = request.POST.get('keyword', '')

        if action == 'disable_all':
            # 关闭所有学习提醒
            preference.enable_learning_reminder = False
            preference.save()
            return JsonResponse({'success': True, 'message': '已关闭所有学习提醒'})

        elif action == 'ignore_keyword':
            # 忽略特定关键词
            import json
            try:
                ignored_keywords = json.loads(preference.ignored_keywords) if preference.ignored_keywords else []
            except (json.JSONDecodeError, TypeError):
                ignored_keywords = []

            if keyword and keyword.lower() not in [k.lower() for k in ignored_keywords]:
                ignored_keywords.append(keyword)
                preference.ignored_keywords = json.dumps(ignored_keywords)
                preference.save()

            return JsonResponse({'success': True, 'message': f'已忽略关键词: {keyword}'})

        elif action == 'enable':
            # 启用学习提醒
            preference.enable_learning_reminder = True
            preference.save()
            return JsonResponse({'success': True, 'message': '学习提醒已启用'})

        elif action == 'clear_ignored':
            # 清空忽略的关键词
            preference.ignored_keywords = ''
            preference.save()
            return JsonResponse({'success': True, 'message': '已清空忽略列表'})

        return JsonResponse({'success': False, 'message': '无效的操作'})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


@require_POST
def update_content_filter_preference(request):
    """更新学习内容提醒偏好设置"""
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': '请先登录'})

    try:
        # 获取或创建用户偏好设置
        preference, created = UserPreference.objects.get_or_create(user=request.user)

        enable_content_filter = request.POST.get('enable_content_filter') == 'true'
        preference.enable_content_filter_reminder = enable_content_filter
        preference.save()

        message = '学习内容提醒已启用' if enable_content_filter else '学习内容提醒已关闭'
        return JsonResponse({'success': True, 'message': message})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'更新失败: {str(e)}'})


@require_POST
def update_theme_preference(request):
    """更新主题偏好设置"""
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': '请先登录'})

    try:
        # 获取或创建用户偏好设置
        preference, created = UserPreference.objects.get_or_create(user=request.user)

        theme = request.POST.get('theme')
        if theme in ['light', 'dark']:
            preference.theme_preference = theme
            preference.save()

            theme_name = '白天模式' if theme == 'light' else '夜间模式'
            return JsonResponse({'success': True, 'message': f'已切换到{theme_name}'})
        else:
            return JsonResponse({'success': False, 'message': '无效的主题选择'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'更新失败: {str(e)}'})


@require_POST
def ai_chat(request):
    """处理AI聊天请求"""
    try:
        # 获取用户输入
        user_message = request.POST.get('message', '').strip()
        chat_type = request.POST.get('type', 'general')  # general, study_plan, progress_analysis
        course_id = request.POST.get('course_id', '')
        ai_model = request.POST.get('ai_model', 'gemini')  # gemini, deepseek

        if not user_message:
            return JsonResponse({
                'success': False,
                'error': '请输入您的问题'
            })

        # 配置Gemini API
        try:
            import google.generativeai as genai
        except ImportError:
            return JsonResponse({
                'success': False,
                'error': '请先安装google-generativeai包: pip install google-generativeai'
            })

        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            return JsonResponse({
                'success': False,
                'error': 'AI服务暂时不可用，请配置GEMINI_API_KEY环境变量'
            })

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')  # 使用更稳定的模型

        # 获取或创建会话ID
        session_id = request.session.get('chat_session_id')
        if not session_id:
            import uuid
            session_id = f"user_{request.user.id if request.user.is_authenticated else 'anonymous'}_{uuid.uuid4().hex[:8]}"
            request.session['chat_session_id'] = session_id

        # 获取最近的聊天历史（最多5条）
        chat_history = ""
        try:
            from .models import ChatHistory
            recent_chats = ChatHistory.objects.filter(session_id=session_id).order_by('-created_at')[:5]
            if recent_chats:
                chat_history = "\n\n最近的对话历史：\n"
                for chat in reversed(recent_chats):  # 按时间正序显示
                    chat_history += f"用户：{chat.user_message}\n"
                    chat_history += f"AI：{chat.ai_response[:200]}...\n\n"  # 限制长度
        except:
            pass

        # 根据聊天类型和模型构建不同的提示词
        system_prompt = get_system_prompt(chat_type, course_id, request, ai_model)
        full_prompt = f"{system_prompt}{chat_history}\n\n当前用户问题：{user_message}"

        # 根据选择的模型调用不同的API
        try:
            # 根据选择的模型调用不同的API
            import requests
            import time

            if ai_model == 'deepseek':
                ai_response = call_deepseek_api(system_prompt, user_message, chat_history)
            else:  # 默认使用gemini
                api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

                headers = {
                    'Content-Type': 'application/json',
                }

                data = {
                    "contents": [{
                        "parts": [{
                            "text": full_prompt
                        }]
                    }]
                }

                # 重试机制：最多重试3次
                max_retries = 3
                ai_response = None

                for attempt in range(max_retries):
                    try:
                        response = requests.post(api_url, headers=headers, json=data, timeout=30)

                        if response.status_code == 200:
                            result = response.json()
                            if 'candidates' in result and len(result['candidates']) > 0:
                                ai_response = result['candidates'][0]['content']['parts'][0]['text']
                                break  # 成功获取回复，跳出重试循环
                            else:
                                ai_response = "抱歉，我暂时无法生成回复，请稍后再试。"
                                break
                        elif response.status_code == 503:
                            # 服务不可用，等待后重试
                            if attempt < max_retries - 1:
                                print(f"API返回503，第{attempt + 1}次重试...")
                                time.sleep(2 ** attempt)  # 指数退避：2秒、4秒、8秒
                                continue
                            else:
                                return JsonResponse({
                                    'success': False,
                                    'error': f'AI服务暂时不可用（HTTP 503），已重试{max_retries}次。请稍后再试，或检查网络连接。'
                                })
                        elif response.status_code == 429:
                            # 请求过于频繁
                            return JsonResponse({
                                'success': False,
                                'error': 'AI服务请求过于频繁，请稍后再试。'
                            })
                        else:
                            print(f"REST API调用失败: {response.status_code}, {response.text}")
                            return JsonResponse({
                                'success': False,
                                'error': f'AI服务返回错误：HTTP {response.status_code}。请检查网络连接或稍后再试。'
                            })

                    except requests.exceptions.Timeout:
                        if attempt < max_retries - 1:
                            print(f"请求超时，第{attempt + 1}次重试...")
                            time.sleep(1)
                            continue
                        else:
                            return JsonResponse({
                                'success': False,
                                'error': 'AI服务响应超时，请稍后再试。'
                            })
                    except requests.exceptions.ConnectionError:
                        if attempt < max_retries - 1:
                            print(f"连接失败，第{attempt + 1}次重试...")
                            time.sleep(1)
                            continue
                        else:
                            return JsonResponse({
                                'success': False,
                                'error': '网络连接失败，无法访问Google AI服务。请检查网络连接，可能需要使用代理。'
                            })

                if not ai_response or ai_response.strip() == '':
                    ai_response = "抱歉，我暂时无法生成回复，请稍后再试。"

            # 保存聊天历史
            try:
                from .models import ChatHistory
                ChatHistory.objects.create(
                    session_id=session_id,
                    user_message=user_message,
                    ai_response=ai_response,
                    chat_type=chat_type
                )
            except Exception as e:
                print(f"保存聊天历史失败: {str(e)}")

        except Exception as api_error:
            print(f"AI API调用错误: {str(api_error)}")
            return JsonResponse({
                'success': False,
                'error': f'AI服务出现错误：{str(api_error)[:100]}'
            })

        return JsonResponse({
            'success': True,
            'response': ai_response,
            'type': chat_type
        })

    except Exception as e:
        print(f"AI聊天错误: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': '抱歉，AI助手暂时无法回应，请稍后再试'
        })


def call_deepseek_api(system_prompt, user_message, chat_history=""):
    """调用DeepSeek API"""
    try:
        import requests
        import json

        # 🔑 DeepSeek API密钥
        api_key = os.getenv('DEEPSEEK_API_KEY')

        # 详细的调试信息
        print(f"[DEBUG] 开始调用DeepSeek API")
        print(f"[DEBUG] API Key前缀: {api_key[:10]}...")

        # 先测试基本网络连接
        try:
            test_response = requests.get("https://www.baidu.com", timeout=5)
            print(f"[DEBUG] 基本网络连接正常: {test_response.status_code}")
        except Exception as e:
            print(f"[DEBUG] 基本网络连接失败: {str(e)}")
            return f"网络连接失败：{str(e)}。请检查网络设置。"

        # 测试DeepSeek域名解析
        try:
            test_response = requests.get("https://api.deepseek.com", timeout=10)
            print(f"[DEBUG] DeepSeek域名连接: {test_response.status_code}")
        except Exception as e:
            print(f"[DEBUG] DeepSeek域名连接失败: {str(e)}")
            return f"无法连接到DeepSeek服务器：{str(e)}。可能是DNS问题或防火墙阻止。"

        api_url = "https://api.deepseek.com/v1/chat/completions"
        print(f"[DEBUG] API URL: {api_url}")

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'BiliStudy/1.0'
        }

        # 构建消息列表
        messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]

        # 如果有聊天历史，添加到消息中
        if chat_history.strip():
            messages.append({
                "role": "assistant",
                "content": f"以下是我们之前的对话历史：{chat_history}"
            })

        # 添加当前用户消息
        messages.append({
            "role": "user",
            "content": user_message
        })

        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "stream": False,
            "max_tokens": 2000,
            "temperature": 0.7
        }

        print(f"[DEBUG] 发送API请求...")
        response = requests.post(api_url, headers=headers, json=data, timeout=60)

        print(f"[DEBUG] 响应状态码: {response.status_code}")
        print(f"[DEBUG] 响应头: {dict(response.headers)}")

        if response.status_code == 200:
            result = response.json()
            print(f"[DEBUG] 响应内容: {json.dumps(result, ensure_ascii=False, indent=2)}")

            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                print(f"[DEBUG] 成功获取回复，长度: {len(content)}")
                return content
            else:
                print(f"[DEBUG] API返回格式异常: {result}")
                return "DeepSeek API返回了空响应，请稍后再试。"
        else:
            error_text = response.text
            print(f"[DEBUG] API调用失败: {response.status_code}")
            print(f"[DEBUG] 错误详情: {error_text}")

            if response.status_code == 402:
                return "DeepSeek API调用失败：账户余额不足或需要付费。请检查您的DeepSeek账户余额。"
            elif response.status_code == 401:
                return "DeepSeek API调用失败：API密钥无效。请检查您的API密钥是否正确。"
            elif response.status_code == 429:
                return "DeepSeek API调用失败：请求过于频繁。请稍后再试。"
            elif response.status_code == 403:
                return "DeepSeek API调用失败：访问被拒绝。请检查API密钥权限。"
            else:
                return f"DeepSeek API调用失败：HTTP {response.status_code}。错误详情：{error_text[:200]}"

    except requests.exceptions.ConnectionError as e:
        print(f"[DEBUG] 连接错误: {str(e)}")
        return f"无法连接到DeepSeek API服务：{str(e)}。请检查网络连接、DNS设置或防火墙配置。"
    except requests.exceptions.Timeout as e:
        print(f"[DEBUG] 超时错误: {str(e)}")
        return "DeepSeek API响应超时，请稍后再试。"
    except requests.exceptions.SSLError as e:
        print(f"[DEBUG] SSL错误: {str(e)}")
        return f"SSL连接错误：{str(e)}。请检查网络安全设置。"
    except Exception as e:
        print(f"[DEBUG] 未知错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"DeepSeek API调用出现错误：{str(e)[:200]}"


def get_system_prompt(chat_type, course_id, request, ai_model='gemini'):
    """根据聊天类型和AI模型生成系统提示词"""
    if ai_model == 'deepseek':
        base_prompt = """你是一个专业的B站学习助手，专门帮助用户进行视频学习。你具有联网搜索能力。

重要指导原则：
1. 如果信息不足，可以先询问1-2个关键问题，然后就给出建议
2. 不要反复询问用户的学习目标和基础，问完1-2个关键问题后就给出专业的解答和建议
3. 结合bilibili网站的视频以及看视频自学这种学习方式的特性给用户提供解答
4. 优先推荐具体的B站UP主、频道和视频

你的核心任务：
- 联网搜索最新的学习资源和信息
- 推荐具体的B站UP主、频道和优质视频，并给出具体的视频链接（点击视频标题可跳转）
- 根据用户需求搜索相关的学习内容
- 提供最新的学习趋势和热门课程
- 将用户提出的笼统的学习目标进行知识领域的细化

回复格式要求：
- 使用markdown格式，包含标题、列表、表格等
- 内容要丰富详细，提供多个学习选择
- 包含学习路径和时间安排建议
- 提供不同难度级别的资源
- 给出具体的实践练习建议

回复结构示例：
# 学习主题

## 学习路径
1. 基础阶段（建议时间）
2. 进阶阶段（建议时间）
3. 实践阶段（建议时间）

## 推荐资源

| 类型 | UP主 | 视频标题 | 难度 | 特点 |
|------|------|----------|------|------|
| 基础教程 | UP主：某某老师 | 视频：基础入门教程 | 初级 | 系统全面 |

## 学习建议
- 具体的学习方法和技巧
- 每日/每周学习时间安排
- 实践项目推荐
- 常见问题解答

回答风格：友好、专业、详细、有帮助。用中文回答。优先提供具体的B站资源推荐。"""
    else:
        base_prompt = """你是一个专业的B站视频学习助手，专门帮助用户进行视频学习。

重要指导原则：
1. 如果信息不足，可以先询问1-2个关键问题，然后就给出建议
2. 不要反复询问用户的学习目标和基础，问完1-2个关键问题后就给出专业的解答和建议
3. 结合bilibili网站的视频以及看视频自学这种学习方式的特性给用户提供解答

你的核心任务：
- 根据用户用户的课程信息和学习统计，根除有针对性的建议
- 将用户提出的笼统的学习目标进行知识领域的细化，帮助用户更好地了解相关内容
- 制定具体的学习计划和时间安排
- 推荐学习方法和技巧
- 分析学习进度并给出改进建议
- 提供学习动机和激励

回答风格：友好、专业、直接、有帮助。用中文回答。"""

    if chat_type == 'study_plan':
        # 获取用户课程信息
        courses_info = ""
        if course_id and request.user.is_authenticated:
            try:
                course = UserCourse.objects.get(id=course_id, user=request.user)
                progress = LearningProgress.objects.filter(user_course=course)
                total_episodes = progress.count()
                completed_episodes = progress.filter(is_completed=True).count()

                courses_info = f"""
当前课程信息：
- 课程名称：{course.custom_title or course.video.title}
- 总分集数：{total_episodes}
- 已完成：{completed_episodes}
- 完成率：{(completed_episodes/total_episodes*100) if total_episodes > 0 else 0:.1f}%
- UP主：{course.video.author}
"""
            except:
                pass
        elif request.user.is_authenticated:
            user_courses = UserCourse.objects.filter(user=request.user)[:5]
            if user_courses:
                courses_info = "用户当前课程：\n"
                for course in user_courses:
                    courses_info += f"- {course.custom_title or course.video.title}\n"

        return f"""{base_prompt}

你现在专门负责制定学习计划。若信息不全，可适当向用户询问1-2个关键问题，然后请直接给出具体的学习建议，不要过多询问用户信息。

{courses_info}

请提供以下具体建议：
1. 每日学习时间安排（如：每天1-2小时，分早晚两次）
2. 学习顺序和重点（比如先学基础，再学进阶）
3. 具体的学习方法（比如做笔记、暂停思考、实践练习）
4. 学习进度安排（如：第1周完成前5集）
5. 学习效果检验（比如定期复习、做练习题）

如果用户没有具体课程，请推荐一些优质的B站学习频道。"""

    elif chat_type == 'progress_analysis':
        # 获取当前用户的学习进度统计
        if request.user.is_authenticated:
            total_courses = UserCourse.objects.filter(user=request.user).count()
            total_progress = LearningProgress.objects.filter(user_course__user=request.user).count()
            completed_progress = LearningProgress.objects.filter(user_course__user=request.user, is_completed=True).count()
        else:
            total_courses = 0
            total_progress = 0
            completed_progress = 0
        completion_rate = (completed_progress / total_progress * 100) if total_progress > 0 else 0

        progress_info = f"""
学习进度统计：
- 总课程数：{total_courses}
- 总学习项目：{total_progress}
- 已完成项目：{completed_progress}
- 整体完成率：{completion_rate:.1f}%
"""

        return f"""{base_prompt}

你现在专门负责分析学习进度。请根据用户的学习数据，提供深入的分析和建议。

{progress_info}

请重点关注：
1. 学习进度分析
2. 学习效率评估
3. 改进建议
4. 激励和鼓励"""

    else:  # general
        return f"""{base_prompt}

你现在是一个通用的学习助手。请直接回答用户问题，给出具体可行的建议。

你可以帮助用户：
1. 推荐B站优质学习频道和UP主
2. 提供学习方法和技巧
3. 制定时间管理方案
4. 解决学习动机问题
5. 回答知识理解和记忆相关问题

重要：请直接给出具体建议，不要反复询问用户的基础情况。如果需要了解更多信息，最多问1个问题后就给出建议。

如果用户询问视频推荐，请推荐一些知名的B站学习UP主以及高质量的b站视频"""


def get_chat_history(request):
    """获取聊天历史记录"""
    try:
        session_id = request.session.get('chat_session_id')
        if not session_id:
            return JsonResponse({
                'success': True,
                'history': []
            })

        from .models import ChatHistory
        history = ChatHistory.objects.filter(session_id=session_id).order_by('created_at')[:20]

        history_data = []
        for record in history:
            history_data.append({
                'user_message': record.user_message,
                'ai_response': record.ai_response,
                'chat_type': record.chat_type,
                'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S')
            })

        return JsonResponse({
            'success': True,
            'history': history_data
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


def check_ai_status(request):
    """检查AI服务状态"""
    try:
        import requests

        # 检查Gemini API
        gemini_api_key =  os.getenv('GEMINI_API_KEY')
        gemini_test_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_api_key}"

        gemini_response = requests.get(gemini_test_url, timeout=10)

        # 检查DeepSeek API
        deepseek_api_key = os.getenv('DEEPSEEK_API_KEY')
        deepseek_test_url = "https://api.deepseek.com/v1/models"
        deepseek_headers = {
            'Authorization': f'Bearer {deepseek_api_key}',
            'Content-Type': 'application/json'
        }

        deepseek_response = requests.get(deepseek_test_url, headers=deepseek_headers, timeout=10)

        result = {
            'gemini': {
                'status': 'normal' if gemini_response.status_code == 200 else 'error',
                'status_code': gemini_response.status_code,
                'message': 'API服务正常' if gemini_response.status_code == 200 else f'HTTP {gemini_response.status_code}'
            },
            'deepseek': {
                'status': 'normal' if deepseek_response.status_code == 200 else 'error',
                'status_code': deepseek_response.status_code,
                'message': 'API服务正常' if deepseek_response.status_code == 200 else _get_deepseek_error_message(deepseek_response.status_code)
            }
        }

        return JsonResponse({
            'success': True,
            'apis': result
        })

    except requests.exceptions.ConnectionError:
        return JsonResponse({
            'success': False,
            'status': 'API连接失败',
            'error': '无法连接到AI服务，可能需要代理或检查网络连接'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'status': 'API检查失败',
            'error': str(e)
        })

def _get_deepseek_error_message(status_code):
    """获取DeepSeek错误信息"""
    error_messages = {
        401: "API密钥无效",
        402: "账户余额不足或需要付费",
        429: "请求过于频繁",
        500: "服务器内部错误"
    }
    return error_messages.get(status_code, f"HTTP {status_code}")


# 学习计划相关视图函数
@login_required
def study_plans(request):
    """我的计划页面"""
    plans = StudyPlan.objects.filter(user=request.user, is_active=True).order_by('-created_at')

    # 计算统计信息
    total_plans = plans.count()
    completed_plans = sum(1 for plan in plans if plan.progress_percentage >= 100)
    overdue_plans = sum(1 for plan in plans if plan.is_overdue and plan.progress_percentage < 100)

    return render(request, 'bilistudy/study_plans.html', {
        'plans': plans,
        'total_plans': total_plans,
        'completed_plans': completed_plans,
        'overdue_plans': overdue_plans,
    })


@require_POST
@login_required
def create_study_plan(request):
    """创建学习计划"""
    try:
        course_id = request.POST.get('course_id')
        total_days = request.POST.get('total_days')
        daily_minutes = request.POST.get('daily_minutes')
        focus_modules = request.POST.get('focus_modules', '')

        # 验证输入
        if not course_id or not total_days or not daily_minutes:
            return JsonResponse({
                'success': False,
                'error': '请填写完整的计划信息'
            })

        try:
            total_days = int(total_days)
            daily_minutes = int(daily_minutes)
        except ValueError:
            return JsonResponse({
                'success': False,
                'error': '天数和时间必须是数字'
            })

        if total_days <= 0 or daily_minutes <= 0:
            return JsonResponse({
                'success': False,
                'error': '天数和时间必须大于0'
            })

        # 获取课程
        user_course = get_object_or_404(UserCourse, id=course_id, user=request.user)

        # 检查是否已有计划
        if hasattr(user_course, 'study_plan'):
            return JsonResponse({
                'success': False,
                'error': '该课程已有学习计划'
            })

        # 创建学习计划
        study_plan = StudyPlan.objects.create(
            user=request.user,
            user_course=user_course,
            total_days=total_days,
            daily_minutes=daily_minutes,
            focus_modules=focus_modules
        )

        return JsonResponse({
            'success': True,
            'message': '学习计划创建成功！',
            'plan_id': study_plan.id
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'创建计划失败：{str(e)}'
        })


@login_required
def plan_detail(request, plan_id):
    """学习计划详情页"""
    plan = get_object_or_404(StudyPlan, id=plan_id, user=request.user)

    # 获取每日学习记录
    daily_records = plan.daily_records.all()

    # 获取课程进度详情（用于分集选择）
    course_progress = LearningProgress.objects.filter(user_course=plan.user_course).order_by('episode__order')

    # 获取课程进度统计
    total_episodes = plan.user_course.video.episodes.count()
    completed_episodes = LearningProgress.objects.filter(
        user_course=plan.user_course,
        is_completed=True
    ).count()

    # 计算统计信息
    # 使用实际完成分集的时长来计算总学习时长
    total_study_minutes = plan.get_total_completed_duration()
    study_days_count = daily_records.count()

    return render(request, 'bilistudy/plan_detail.html', {
        'plan': plan,
        'daily_records': daily_records,
        'course_progress': course_progress,  # 新增：分集进度详情
        'total_episodes': total_episodes,
        'completed_episodes': completed_episodes,
        'total_study_minutes': total_study_minutes,
        'study_days_count': study_days_count,
    })


@require_POST
@login_required
def update_daily_record(request, plan_id):
    """更新每日学习记录"""
    try:
        plan = get_object_or_404(StudyPlan, id=plan_id, user=request.user)
        study_date = request.POST.get('study_date')
        study_minutes = request.POST.get('study_minutes', 0)
        notes = request.POST.get('notes', '')
        completed_episodes = request.POST.getlist('completed_episodes[]')



        if not study_date:
            return JsonResponse({
                'success': False,
                'error': '请选择学习日期'
            })

        from datetime import datetime
        study_date = datetime.strptime(study_date, '%Y-%m-%d').date()

        try:
            study_minutes = int(study_minutes)
        except ValueError:
            study_minutes = 0

        # 获取或创建每日记录
        daily_record, created = DailyStudyRecord.objects.get_or_create(
            study_plan=plan,
            study_date=study_date,
            defaults={
                'study_minutes': study_minutes,
                'notes': notes
            }
        )

        if not created:
            daily_record.study_minutes = study_minutes
            daily_record.notes = notes
            daily_record.save()

        # 处理完成的分集
        completed_count = 0
        if completed_episodes:
            from django.utils import timezone
            current_time = timezone.now()

            for progress_id in completed_episodes:
                try:
                    progress = LearningProgress.objects.get(
                        id=progress_id,
                        user_course=plan.user_course
                    )

                    # 只有未完成的分集才能被标记为完成
                    if not progress.is_completed:
                        progress.is_completed = True
                        progress.completed_at = current_time
                        progress.save()
                        completed_count += 1

                except LearningProgress.DoesNotExist:
                    continue

        return JsonResponse({
            'success': True,
            'message': '学习记录更新成功！',
            'completed_episodes_count': completed_count
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'更新失败：{str(e)}'
        })


@require_POST
@login_required
def delete_study_plan(request, plan_id):
    """删除学习计划"""
    try:
        plan = get_object_or_404(StudyPlan, id=plan_id, user=request.user)
        plan.delete()

        messages.success(request, '学习计划已删除')
        return redirect('study_plans')

    except Exception as e:
        messages.error(request, f'删除失败：{str(e)}')
        return redirect('study_plans')


# ==================== 用户认证相关视图函数 ====================

def send_verification_email(email, code, purpose):
    """发送验证码邮件"""
    try:
        subject_map = {
            'register': 'B站学习助手 - 注册验证码',
            'reset_password': 'B站学习助手 - 密码重置验证码',
            'change_email': 'B站学习助手 - 邮箱修改验证码',
        }

        subject = subject_map.get(purpose, 'B站学习助手 - 验证码')
        message = f'''
您好！

您的验证码是：{code}

此验证码10分钟内有效，请及时使用。
如果这不是您的操作，请忽略此邮件。

B站学习助手团队
        '''

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False


@require_POST
def send_verification_code(request):
    """发送验证码"""
    try:
        email = request.POST.get('email', '').strip()
        purpose = request.POST.get('purpose', 'register')

        if not email:
            return JsonResponse({
                'success': False,
                'message': '请输入邮箱地址'
            })

        # 验证邮箱格式
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            return JsonResponse({
                'success': False,
                'message': '邮箱格式不正确'
            })

        # 检查邮箱是否已注册
        if purpose == 'register':
            if User.objects.filter(email=email).exists():
                return JsonResponse({
                    'success': False,
                    'message': '该邮箱已被注册'
                })
        elif purpose == 'reset_password':
            if not User.objects.filter(email=email).exists():
                return JsonResponse({
                    'success': False,
                    'message': '该邮箱未注册'
                })

        # 生成验证码
        code = EmailVerification.generate_code()

        # 删除该邮箱之前未使用的验证码
        EmailVerification.objects.filter(
            email=email,
            purpose=purpose,
            is_used=False
        ).delete()

        # 创建新的验证码记录
        EmailVerification.objects.create(
            email=email,
            code=code,
            purpose=purpose
        )

        # 发送邮件
        if send_verification_email(email, code, purpose):
            return JsonResponse({
                'success': True,
                'message': '验证码已发送到您的邮箱，请查收'
            })
        else:
            return JsonResponse({
                'success': False,
                'message': '邮件发送失败，请稍后重试'
            })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'发送失败：{str(e)}'
        })


@require_POST
def register_user(request):
    """用户注册"""
    try:
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()
        verification_code = request.POST.get('verification_code', '').strip()

        # 基本验证
        if not all([username, email, password, confirm_password, verification_code]):
            return JsonResponse({
                'success': False,
                'message': '请填写所有必填字段'
            })

        if password != confirm_password:
            return JsonResponse({
                'success': False,
                'message': '两次输入的密码不一致'
            })

        if len(password) < 6:
            return JsonResponse({
                'success': False,
                'message': '密码长度至少6位'
            })

        # 检查用户名是否已存在
        if User.objects.filter(username=username).exists():
            return JsonResponse({
                'success': False,
                'message': '用户名已存在'
            })

        # 检查邮箱是否已存在
        if User.objects.filter(email=email).exists():
            return JsonResponse({
                'success': False,
                'message': '邮箱已被注册'
            })

        # 验证验证码
        verification = EmailVerification.objects.filter(
            email=email,
            code=verification_code,
            purpose='register',
            is_used=False
        ).first()

        if not verification:
            return JsonResponse({
                'success': False,
                'message': '验证码无效'
            })

        if verification.is_expired():
            return JsonResponse({
                'success': False,
                'message': '验证码已过期'
            })

        # 创建用户
        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password
            )

            # 标记验证码为已使用
            verification.is_used = True
            verification.save()

        return JsonResponse({
            'success': True,
            'message': '注册成功！请登录'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'注册失败：{str(e)}'
        })


@require_POST
def login_user(request):
    """用户登录"""
    try:
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        remember_me = request.POST.get('remember_me') == 'on'

        if not username or not password:
            return JsonResponse({
                'success': False,
                'message': '请输入用户名和密码'
            })

        # 尝试用用户名或邮箱登录
        from django.contrib.auth.models import User

        user_obj = None
        user = None

        if '@' in username:
            # 邮箱登录
            try:
                user_obj = User.objects.get(email=username)
                user = authenticate(request, username=user_obj.username, password=password)
            except User.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'message': '该邮箱未注册'
                })
        else:
            # 用户名登录
            try:
                user_obj = User.objects.get(username=username)
                user = authenticate(request, username=username, password=password)
            except User.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'message': '用户名不存在'
                })

        if user is not None:
            login(request, user)

            # 设置会话过期时间
            if remember_me:
                request.session.set_expiry(30 * 24 * 60 * 60)  # 30天
            else:
                request.session.set_expiry(0)  # 浏览器关闭时过期

            return JsonResponse({
                'success': True,
                'message': '登录成功！',
                'redirect_url': '/'
            })
        else:
            # 用户存在但密码错误
            return JsonResponse({
                'success': False,
                'message': '密码错误'
            })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'登录失败：{str(e)}'
        })


def logout_user(request):
    """用户登出"""
    logout(request)
    messages.success(request, '已成功退出登录')
    return redirect('index')


@require_POST
def reset_password_request(request):
    """请求密码重置"""
    try:
        email = request.POST.get('email', '').strip()
        verification_code = request.POST.get('verification_code', '').strip()

        if not email:
            return JsonResponse({
                'success': False,
                'message': '请输入邮箱地址'
            })

        # 检查用户是否存在
        if not User.objects.filter(email=email).exists():
            return JsonResponse({
                'success': False,
                'message': '该邮箱未注册'
            })

        if verification_code:
            # 验证验证码并显示重置密码表单
            verification = EmailVerification.objects.filter(
                email=email,
                code=verification_code,
                purpose='reset_password',
                is_used=False
            ).first()

            if not verification:
                return JsonResponse({
                    'success': False,
                    'message': '验证码无效'
                })

            if verification.is_expired():
                return JsonResponse({
                    'success': False,
                    'message': '验证码已过期'
                })

            return JsonResponse({
                'success': True,
                'message': '验证码正确，请设置新密码',
                'show_password_form': True
            })

        return JsonResponse({
            'success': False,
            'message': '请先获取验证码'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'操作失败：{str(e)}'
        })


@require_POST
def reset_password_confirm(request):
    """确认密码重置"""
    try:
        email = request.POST.get('email', '').strip()
        verification_code = request.POST.get('verification_code', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if not all([email, verification_code, new_password, confirm_password]):
            return JsonResponse({
                'success': False,
                'message': '请填写所有字段'
            })

        if new_password != confirm_password:
            return JsonResponse({
                'success': False,
                'message': '两次输入的密码不一致'
            })

        if len(new_password) < 6:
            return JsonResponse({
                'success': False,
                'message': '密码长度至少6位'
            })

        # 验证验证码
        verification = EmailVerification.objects.filter(
            email=email,
            code=verification_code,
            purpose='reset_password',
            is_used=False
        ).first()

        if not verification:
            return JsonResponse({
                'success': False,
                'message': '验证码无效'
            })

        if verification.is_expired():
            return JsonResponse({
                'success': False,
                'message': '验证码已过期'
            })

        # 重置密码
        with transaction.atomic():
            user = User.objects.get(email=email)
            user.set_password(new_password)
            user.save()

            # 标记验证码为已使用
            verification.is_used = True
            verification.save()

        return JsonResponse({
            'success': True,
            'message': '密码重置成功！请使用新密码登录'
        })

    except User.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': '用户不存在'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'重置失败：{str(e)}'
        })


@login_required
def account_settings(request):
    """账户设置页面"""
    # 计算学习统计
    user_courses = UserCourse.objects.filter(user=request.user)
    completed_episodes = 0
    total_study_minutes = 0

    for course in user_courses:
        completed_episodes += LearningProgress.objects.filter(
            user_course=course,
            is_completed=True
        ).count()

    # 计算总学习时间（从学习计划的每日记录中）
    study_plans = StudyPlan.objects.filter(user=request.user)
    for plan in study_plans:
        total_study_minutes += sum(
            record.study_minutes for record in plan.daily_records.all()
        )

    # 获取用户偏好设置
    try:
        user_preference = request.user.preference
    except UserPreference.DoesNotExist:
        user_preference = UserPreference.objects.create(user=request.user)

    context = {
        'completed_episodes': completed_episodes,
        'total_study_minutes': total_study_minutes,
        'user_preference': user_preference,
    }
    return render(request, 'bilistudy/account_settings.html', context)


@login_required
@require_POST
def change_password(request):
    """修改密码"""
    try:
        current_password = request.POST.get('current_password', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if not all([current_password, new_password, confirm_password]):
            return JsonResponse({
                'success': False,
                'message': '请填写所有字段'
            })

        if new_password != confirm_password:
            return JsonResponse({
                'success': False,
                'message': '两次输入的新密码不一致'
            })

        if len(new_password) < 6:
            return JsonResponse({
                'success': False,
                'message': '密码长度至少6位'
            })

        # 验证当前密码
        if not request.user.check_password(current_password):
            return JsonResponse({
                'success': False,
                'message': '当前密码错误'
            })

        # 修改密码
        request.user.set_password(new_password)
        request.user.save()

        # 重新登录用户（因为密码改变会使session失效）
        user = authenticate(username=request.user.username, password=new_password)
        if user:
            login(request, user)

        return JsonResponse({
            'success': True,
            'message': '密码修改成功！'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'修改失败：{str(e)}'
        })


@login_required
@require_POST
def change_email_request(request):
    """请求修改邮箱"""
    try:
        current_password = request.POST.get('current_password', '').strip()
        new_email = request.POST.get('new_email', '').strip()
        verification_code = request.POST.get('verification_code', '').strip()

        if not current_password:
            return JsonResponse({
                'success': False,
                'message': '请输入当前密码'
            })

        if not new_email:
            return JsonResponse({
                'success': False,
                'message': '请输入新邮箱'
            })

        # 验证当前密码
        if not request.user.check_password(current_password):
            return JsonResponse({
                'success': False,
                'message': '当前密码错误'
            })

        # 检查新邮箱是否已被使用
        if User.objects.filter(email=new_email).exclude(id=request.user.id).exists():
            return JsonResponse({
                'success': False,
                'message': '该邮箱已被其他用户使用'
            })

        if verification_code:
            # 验证验证码并修改邮箱
            verification = EmailVerification.objects.filter(
                email=new_email,
                code=verification_code,
                purpose='change_email',
                is_used=False
            ).first()

            if not verification:
                return JsonResponse({
                    'success': False,
                    'message': '验证码无效'
                })

            if verification.is_expired():
                return JsonResponse({
                    'success': False,
                    'message': '验证码已过期'
                })

            # 修改邮箱
            with transaction.atomic():
                request.user.email = new_email
                request.user.save()

                # 标记验证码为已使用
                verification.is_used = True
                verification.save()

            return JsonResponse({
                'success': True,
                'message': '邮箱修改成功！'
            })

        return JsonResponse({
            'success': False,
            'message': '请先获取验证码'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'修改失败：{str(e)}'
        })


@require_POST
@login_required
def delete_account(request):
    """注销账号"""
    try:
        password = request.POST.get('password')

        if not password:
            return JsonResponse({
                'success': False,
                'message': '请输入密码'
            })

        # 验证密码
        if not request.user.check_password(password):
            return JsonResponse({
                'success': False,
                'message': '密码错误'
            })

        # 使用事务确保数据一致性
        with transaction.atomic():
            user = request.user
            user_email = user.email

            # 删除用户相关的所有数据
            try:
                # 1. 删除课程列表和学习进度（级联删除会自动删除LearningProgress）
                deleted_courses = UserCourse.objects.filter(user=user).count()
                UserCourse.objects.filter(user=user).delete()

                # 2. 删除学习计划和每日记录（级联删除会自动删除DailyStudyRecord）
                deleted_plans = StudyPlan.objects.filter(user=user).count()
                StudyPlan.objects.filter(user=user).delete()

                # 3. 删除AI聊天历史（通过session_id关联）
                deleted_chats = 0
                try:
                    from .models import ChatHistory
                    # 获取用户的session_id（如果存在）
                    session_id = request.session.get('chat_session_id')
                    if session_id:
                        deleted_chats = ChatHistory.objects.filter(session_id=session_id).count()
                        ChatHistory.objects.filter(session_id=session_id).delete()
                except (ImportError, Exception) as e:
                    # 如果ChatHistory模型不存在或其他错误，记录但不中断流程
                    print(f"删除聊天历史时出错: {str(e)}")

                # 4. 删除邮箱验证记录
                deleted_verifications = EmailVerification.objects.filter(email=user.email).count()
                EmailVerification.objects.filter(email=user.email).delete()

                # 5. 清除session数据
                request.session.flush()

                # 6. 最后删除用户账户（这会自动删除所有外键关联的数据）
                user.delete()

                print(f"成功删除用户 {user_email} 的数据: 课程{deleted_courses}个, 学习计划{deleted_plans}个, 聊天记录{deleted_chats}条, 邮箱验证{deleted_verifications}条")

            except Exception as e:
                print(f"删除用户数据时出错: {str(e)}")
                raise  # 重新抛出异常，让事务回滚

        return JsonResponse({
            'success': True,
            'message': f'账号 {user_email} 已成功注销，所有相关数据已删除'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'注销失败：{str(e)}'
        })


@login_required
@require_POST
def change_username(request):
    """修改用户名"""
    try:
        current_password = request.POST.get('current_password', '').strip()
        new_username = request.POST.get('new_username', '').strip()

        if not current_password or not new_username:
            return JsonResponse({
                'success': False,
                'message': '请填写所有字段'
            })

        # 验证用户名格式
        if len(new_username) < 3 or len(new_username) > 20:
            return JsonResponse({
                'success': False,
                'message': '用户名长度应在3-20个字符之间'
            })

        # 验证用户名是否包含特殊字符
        import re
        if not re.match(r'^[a-zA-Z0-9_\u4e00-\u9fa5]+$', new_username):
            return JsonResponse({
                'success': False,
                'message': '用户名只能包含字母、数字、下划线和中文'
            })

        # 验证当前密码
        if not request.user.check_password(current_password):
            return JsonResponse({
                'success': False,
                'message': '当前密码错误'
            })

        # 检查新用户名是否已被使用
        if User.objects.filter(username=new_username).exclude(id=request.user.id).exists():
            return JsonResponse({
                'success': False,
                'message': '该用户名已被使用'
            })

        # 修改用户名
        request.user.username = new_username
        request.user.save()

        return JsonResponse({
            'success': True,
            'message': '用户名修改成功！',
            'new_username': new_username
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'修改失败：{str(e)}'
        })


@login_required
@require_POST
def update_study_notes(request, plan_id):
    """更新学习记录的备忘"""
    try:
        plan = get_object_or_404(StudyPlan, id=plan_id, user=request.user)
        record_id = request.POST.get('record_id')
        notes = request.POST.get('notes', '').strip()

        if not record_id:
            return JsonResponse({
                'success': False,
                'error': '缺少记录ID'
            })

        # 验证备忘长度
        if len(notes) > 1000:
            return JsonResponse({
                'success': False,
                'error': '学习备忘内容不能超过1000个字符'
            })

        # 获取学习记录
        try:
            record = DailyStudyRecord.objects.get(id=record_id, study_plan=plan)
        except DailyStudyRecord.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': '学习记录不存在'
            })

        # 更新备忘
        record.notes = notes
        record.save()

        return JsonResponse({
            'success': True,
            'message': '学习备忘保存成功！'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'保存失败：{str(e)}'
        })


@login_required
@require_POST
def delete_study_record(request, plan_id):
    """删除学习记录"""
    try:
        plan = get_object_or_404(StudyPlan, id=plan_id, user=request.user)
        record_id = request.POST.get('record_id')
        delete_option = request.POST.get('delete_option', 'record_only')  # 默认只删除记录

        if not record_id:
            return JsonResponse({
                'success': False,
                'error': '缺少记录ID'
            })

        # 获取学习记录
        try:
            record = DailyStudyRecord.objects.get(id=record_id, study_plan=plan)
        except DailyStudyRecord.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': '学习记录不存在'
            })

        reverted_episodes_count = 0

        # 根据删除选项决定是否同时删除学习数据
        if delete_option == 'with_progress':
            # 获取该日完成的分集，需要将它们的状态重置为未完成
            from django.utils import timezone
            from datetime import datetime, time

            study_date = record.study_date
            start_of_day = datetime.combine(study_date, time.min)
            end_of_day = datetime.combine(study_date, time.max)

            # 查找在该日完成的学习进度记录并重置
            user_course = plan.user_course
            daily_progress_records = LearningProgress.objects.filter(
                user_course=user_course,
                is_completed=True,
                completed_at__range=(start_of_day, end_of_day)
            )

            # 重置这些分集的完成状态
            for progress in daily_progress_records:
                progress.is_completed = False
                progress.completed_at = None
                progress.save()
                reverted_episodes_count += 1

        # 删除学习记录
        record.delete()

        return JsonResponse({
            'success': True,
            'message': '学习记录删除成功！',
            'reverted_episodes_count': reverted_episodes_count,
            'delete_option': delete_option
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'删除失败：{str(e)}'
        })


def check_username_availability(request):
    """检查用户名是否可用"""
    username = request.GET.get('username', '').strip()

    if not username:
        return JsonResponse({
            'available': False,
            'message': '请输入用户名'
        })

    # 检查用户名长度
    if len(username) < 3:
        return JsonResponse({
            'available': False,
            'message': '用户名长度不能少于3个字符'
        })

    if len(username) > 20:
        return JsonResponse({
            'available': False,
            'message': '用户名长度不能超过20个字符'
        })

    # 检查是否以数字开头
    if username[0].isdigit():
        return JsonResponse({
            'available': False,
            'message': '用户名不能以数字开头'
        })

    # 检查用户名格式
    import re
    if not re.match(r'^[a-zA-Z0-9_\u4e00-\u9fa5]+$', username):
        return JsonResponse({
            'available': False,
            'message': '用户名只能包含字母、数字、下划线和中文'
        })

    # 检查用户名是否已存在
    from django.contrib.auth.models import User

    if User.objects.filter(username=username).exists():
        return JsonResponse({
            'available': False,
            'message': '该用户名已存在'
        })

    return JsonResponse({
        'available': True,
        'message': '用户名可用'
    })


def check_email_availability(request):
    """检查邮箱是否可用"""
    email = request.GET.get('email', '').strip()

    if not email:
        return JsonResponse({
            'available': False,
            'message': '请输入邮箱地址'
        })

    # 检查邮箱格式
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return JsonResponse({
            'available': False,
            'message': '邮箱格式不正确'
        })

    # 检查邮箱是否已注册
    from django.contrib.auth.models import User

    if User.objects.filter(email=email).exists():
        return JsonResponse({
            'available': False,
            'message': '该邮箱已被注册'
        })

    return JsonResponse({
        'available': True,
        'message': '邮箱可用'
    })



#新手指南界面
def beginner_guide(request):
    """新手指南页面"""
    # 如果用户已登录，标记已查看新手指南
    if request.user.is_authenticated:
        try:
            user_preference = UserPreference.objects.get(user=request.user)
            if not user_preference.has_viewed_guide:
                user_preference.has_viewed_guide = True
                user_preference.save()
        except UserPreference.DoesNotExist:
            # 如果用户偏好不存在，创建一个
            UserPreference.objects.create(
                user=request.user,
                has_viewed_guide=True
            )

    return render(request, 'bilistudy/beginner_guide.html')


@require_POST
def mark_guide_viewed(request):
    """标记用户已查看新手指南"""
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': '用户未登录'})

    try:
        user_preference, created = UserPreference.objects.get_or_create(
            user=request.user,
            defaults={'has_viewed_guide': True}
        )

        if not created and not user_preference.has_viewed_guide:
            user_preference.has_viewed_guide = True
            user_preference.save()

        return JsonResponse({'success': True, 'message': '已标记为已查看'})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


def export_plan_pdf(request, plan_id):
    """导出学习计划PDF报告 - 直接下载PDF文件"""
    if not request.user.is_authenticated:
        from django.shortcuts import redirect
        return redirect('index')

    try:
        from django.http import HttpResponse
        from datetime import datetime
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from io import BytesIO

        # 获取学习计划和相关数据
        plan = get_object_or_404(StudyPlan, id=plan_id, user=request.user)
        daily_records = DailyStudyRecord.objects.filter(study_plan=plan).order_by('study_date')

        # 计算统计数据
        total_study_time = sum(record.study_minutes for record in daily_records)
        completed_days = daily_records.filter(study_minutes__gt=0).count()
        avg_daily_time = total_study_time / max(completed_days, 1)

        # 创建PDF文档
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=18)

        # 注册中文字体（尝试使用系统字体）
        try:
            pdfmetrics.registerFont(TTFont('SimHei', 'C:/Windows/Fonts/simhei.ttf'))
            chinese_font = 'SimHei'
        except:
            try:
                pdfmetrics.registerFont(TTFont('SimSun', 'C:/Windows/Fonts/simsun.ttc'))
                chinese_font = 'SimSun'
            except:
                chinese_font = 'Helvetica'  # 回退到默认字体

        # 创建样式
        styles = getSampleStyleSheet()

        # 自定义样式
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontName=chinese_font,
            fontSize=20,
            spaceAfter=30,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#1e40af')
        )

        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontName=chinese_font,
            fontSize=14,
            spaceAfter=12,
            spaceBefore=20,
            textColor=colors.HexColor('#1e40af')
        )

        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontName=chinese_font,
            fontSize=10,
            spaceAfter=6,
            leading=14
        )

        # 构建PDF内容
        story = []

        # 报告标题和头部信息
        course_title = plan.user_course.custom_title or plan.user_course.video.title
        current_time = datetime.now()

        # 主标题 - 更简洁的设计
        story.append(Paragraph(f"📚 {course_title}", title_style))
        story.append(Paragraph("学习报告", title_style))
        story.append(Spacer(1, 12))

        # 副标题样式
        subtitle_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontName=chinese_font,
            fontSize=11,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#64748b'),
            spaceAfter=24
        )

        story.append(Paragraph(f"报告生成时间：{current_time.strftime('%Y年%m月%d日')}", subtitle_style))
        story.append(Spacer(1, 8))

        # 学习概览卡片
        overview_data = [
            ['📚 课程', course_title],
            ['👤 作者', plan.user_course.video.author],
            ['📅 计划周期', f'{plan.total_days}天'],
            ['⏰ 每日目标', f'{plan.daily_minutes}分钟'],
            ['📈 当前进度', f'{plan.progress_percentage:.1f}%']
        ]

        overview_table = Table(overview_data, colWidths=[1.2*inch, 4.8*inch])
        overview_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#3b82f6')),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.whitesmoke),
            ('BACKGROUND', (1, 0), (1, -1), colors.HexColor('#eff6ff')),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), chinese_font),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (1, 0), (1, -1), [colors.HexColor('#eff6ff')])
        ]))

        story.append(overview_table)
        story.append(Spacer(1, 20))

        # 计算详细统计数据
        completion_rate = (completed_days / max(plan.days_passed, 1)) * 100
        target_vs_actual = (total_study_time / max(plan.days_passed * plan.daily_minutes, 1)) * 100
        remaining_days = max(plan.total_days - plan.days_passed, 0)
        estimated_completion_time = remaining_days * plan.daily_minutes

        # 计算预计完成日期
        from datetime import timedelta
        estimated_end_date = plan.start_date + timedelta(days=plan.total_days - 1)

        # 计算学习效率指标
        if plan.days_passed > 0:
            daily_completion_rate = completion_rate / 100
            learning_efficiency = target_vs_actual / 100
        else:
            daily_completion_rate = 0
            learning_efficiency = 0

        # 学习成果总结
        story.append(Paragraph("📈 学习成果总结", heading_style))

        # 计算关键指标
        study_efficiency = (total_study_time / max(plan.days_passed * plan.daily_minutes, 1)) * 100
        consistency_rate = (completed_days / max(plan.days_passed, 1)) * 100

        # 生成评价
        efficiency_grade = "优秀" if study_efficiency >= 90 else "良好" if study_efficiency >= 70 else "一般" if study_efficiency >= 50 else "待提升"
        consistency_grade = "优秀" if consistency_rate >= 90 else "良好" if consistency_rate >= 70 else "一般" if consistency_rate >= 50 else "待提升"

        summary_data = [
            ['学习指标', '实际表现', '评价等级'],
            ['📚 累计学习', f'{total_study_time}分钟 ({total_study_time//60}小时{total_study_time%60}分钟)', f'已完成 {study_efficiency:.1f}% 目标'],
            ['📅 坚持天数', f'{completed_days}/{plan.days_passed}天', f'{consistency_grade} ({consistency_rate:.1f}%)'],
            ['⏰ 平均时长', f'{avg_daily_time:.0f}分钟/天', efficiency_grade],
            ['🎯 剩余计划', f'{remaining_days}天', f'预计需要 {estimated_completion_time//60}小时'],
        ]

        overview_table = Table(overview_data, colWidths=[2*inch, 2*inch, 2*inch])
        overview_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#eff6ff')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), chinese_font),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#eff6ff')])
        ]))

        story.append(overview_table)
        story.append(Spacer(1, 20))



        # 最近学习记录
        if daily_records.exists():
            story.append(Paragraph("📅 最近学习记录", heading_style))

            # 只显示最近10天的记录
            recent_records = daily_records.order_by('-study_date')[:10]
            records_table_data = [['日期', '实际时长', '完成率', '状态']]

            for record in recent_records:
                completion_rate_val = (record.study_minutes / max(plan.daily_minutes, 1)) * 100 if plan.daily_minutes > 0 else 0

                if record.study_minutes >= plan.daily_minutes:
                    status = '✅ 达标'
                elif record.study_minutes > 0:
                    status = '⚠️ 部分'
                else:
                    status = '❌ 未学'

                records_table_data.append([
                    record.study_date.strftime('%m-%d'),
                    f'{record.study_minutes}分钟',
                    f'{min(completion_rate_val, 100):.0f}%',
                    status
                ])

            records_table = Table(records_table_data, colWidths=[1*inch, 1.5*inch, 1*inch, 1.5*inch])
            records_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3b82f6')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), chinese_font),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')])
            ]))

            story.append(records_table)
            story.append(Spacer(1, 16))

        else:
            story.append(Paragraph("📅 最近学习记录", heading_style))
            story.append(Paragraph("暂无学习记录，建议开始记录每日学习情况。", normal_style))

        story.append(Spacer(1, 20))

        # 六、专业分析与改进建议
        story.append(Paragraph("六、专业分析与改进建议", heading_style))

        # 简化的学习建议
        if daily_records.exists():
            excellent_count = sum(1 for record in daily_records if record.study_minutes >= plan.daily_minutes)
            excellent_rate = (excellent_count / len(daily_records)) * 100
            overall_performance = "优秀" if excellent_rate >= 70 else "良好" if excellent_rate >= 50 else "一般" if excellent_rate >= 30 else "待改进"
        else:
            excellent_rate = 0
            overall_performance = "无数据"

        # 生成专业分析报告
        analysis_data = [
            ['分析维度', '当前状况', '专业评估', '具体建议'],
            ['整体表现', f'{overall_performance} (优秀率{excellent_rate:.1f}%)',
             '优秀' if excellent_rate >= 70 else '良好' if excellent_rate >= 50 else '需改进',
             '继续保持高标准' if excellent_rate >= 70 else '提升学习质量和时长'],
            ['学习规律', f'平均{avg_daily_time:.1f}分钟/天',
             '规律' if abs(avg_daily_time - plan.daily_minutes) <= 15 else '不够规律',
             '保持当前节奏' if abs(avg_daily_time - plan.daily_minutes) <= 15 else '建议固定学习时间段'],
            ['目标达成', f'{target_vs_actual:.1f}%完成度',
             '达标' if target_vs_actual >= 80 else '接近达标' if target_vs_actual >= 60 else '未达标',
             '保持现状' if target_vs_actual >= 80 else '需要增加学习投入'],
            ['学习坚持', f'{completion_rate:.1f}%出勤率',
             '优秀' if completion_rate >= 85 else '良好' if completion_rate >= 70 else '一般',
             '继续保持' if completion_rate >= 70 else '设置学习提醒和激励机制']
        ]

        analysis_table = Table(analysis_data, colWidths=[1.2*inch, 1.5*inch, 1.3*inch, 2*inch])
        analysis_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#7c3aed')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#f3e8ff')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), chinese_font),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f3e8ff')])
        ]))

        story.append(analysis_table)
        story.append(Spacer(1, 16))

        # 个性化改进建议
        story.append(Paragraph("个性化改进建议：", heading_style))

        # 根据实际数据生成针对性建议
        suggestions = []

        # 基于学习坚持性的建议
        if completion_rate >= 90:
            suggestions.append("1. 学习坚持性表现优异，建议挑战更高难度的学习内容。")
        elif completion_rate >= 70:
            suggestions.append("1. 学习坚持性良好，建议设定每周学习目标来进一步提升。")
        else:
            suggestions.append("1. 学习坚持性需要改进，建议使用番茄工作法，每次学习25分钟。")

        # 基于学习时长的建议
        if target_vs_actual >= 100:
            suggestions.append("2. 学习时长已达标，可以考虑增加学习深度和复习频率。")
        elif target_vs_actual >= 80:
            suggestions.append("2. 学习时长接近目标，建议每天增加10-15分钟的复习时间。")
        else:
            suggestions.append("2. 学习时长不足，建议将大块学习时间分解为多个小时段。")

        # 基于学习规律的建议
        if daily_records.exists():
            weekend_study = sum(1 for record in daily_records if record.study_date.weekday() >= 5 and record.study_minutes > 0)
            weekday_study = sum(1 for record in daily_records if record.study_date.weekday() < 5 and record.study_minutes > 0)

            if weekend_study > 0 and weekday_study > 0:
                suggestions.append("3. 工作日和周末都有学习记录，学习安排较为均衡。")
            elif weekday_study > weekend_study:
                suggestions.append("3. 工作日学习较多，建议周末也保持一定的学习强度。")
            else:
                suggestions.append("3. 建议在工作日也安排固定的学习时间，保持学习连续性。")

        # 通用专业建议
        suggestions.extend([
            "4. 建议使用费曼学习法：学完后尝试向他人解释所学内容。",
            "5. 定期进行学习回顾，每周总结学习成果和遇到的问题。",
            "6. 建立学习社群，与同样在学习的伙伴互相监督和鼓励。"
        ])

        for suggestion in suggestions:
            story.append(Paragraph(suggestion, normal_style))
            story.append(Spacer(1, 8))

        story.append(Spacer(1, 24))

        # 报告总结
        story.append(Paragraph("七、报告总结", heading_style))

        # 生成总结性评价
        if daily_records.exists():
            total_planned_time = plan.days_passed * plan.daily_minutes
            efficiency_score = min((total_study_time / max(total_planned_time, 1)) * 100, 100)
            consistency_score = completion_rate
            overall_score = (efficiency_score + consistency_score) / 2

            if overall_score >= 85:
                grade = "A (优秀)"
                summary_text = "您的学习表现非常出色，已经建立了良好的学习习惯。"
            elif overall_score >= 70:
                grade = "B (良好)"
                summary_text = "您的学习表现良好，继续保持并适当提升学习强度。"
            elif overall_score >= 60:
                grade = "C (一般)"
                summary_text = "您的学习表现一般，需要在坚持性和效率方面有所改进。"
            else:
                grade = "D (待改进)"
                summary_text = "您的学习表现有待提升，建议重新规划学习计划。"
        else:
            grade = "N/A (无数据)"
            summary_text = "暂无足够数据进行评估，建议开始记录学习情况。"

        summary_content = f"""
        <b>综合评估等级：</b>{grade}<br/>
        <b>学习效率得分：</b>{efficiency_score:.1f}分 (满分100分)<br/>
        <b>学习坚持得分：</b>{consistency_score:.1f}分 (满分100分)<br/>
        <b>综合表现得分：</b>{overall_score:.1f}分 (满分100分)<br/><br/>
        <b>总结评价：</b>{summary_text}
        """

        story.append(Paragraph(summary_content, normal_style))
        story.append(Spacer(1, 20))

        # 专业报告页脚
        footer_style = ParagraphStyle(
            'Footer',
            parent=styles['Normal'],
            fontName=chinese_font,
            fontSize=8,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#64748b')
        )

        divider_style = ParagraphStyle(
            'Divider',
            parent=styles['Normal'],
            fontName=chinese_font,
            fontSize=10,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#3b82f6')
        )

        story.append(Paragraph("═" * 60, divider_style))
        story.append(Spacer(1, 8))
        story.append(Paragraph("B站学习工具 - 个人学习分析系统", footer_style))
        story.append(Paragraph(f"报告编号：RPT-{plan.id}-{current_time.strftime('%Y%m%d%H%M')}", footer_style))
        story.append(Paragraph(f"生成时间：{current_time.strftime('%Y年%m月%d日 %H:%M:%S')}", footer_style))
        story.append(Paragraph("本报告基于真实学习数据生成，仅供个人学习参考", footer_style))
        story.append(Spacer(1, 8))
        story.append(Paragraph("持续学习，成就更好的自己！", footer_style))

        # 生成PDF
        doc.build(story)

        # 创建HTTP响应
        pdf = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf, content_type='application/pdf')

        # 清理文件名，移除特殊字符
        import re
        clean_title = re.sub(r'[<>:"/\\|?*]', '', course_title)
        clean_title = clean_title.replace(' ', '_')[:30]  # 限制长度并替换空格

        current_date = datetime.now().strftime("%Y%m%d")
        filename = f'学习计划详细报告_{clean_title}_{current_date}.pdf'

        # 使用URL编码确保中文文件名正确显示
        from urllib.parse import quote
        encoded_filename = quote(filename.encode('utf-8'))
        response['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{encoded_filename}'

        return response

    except Exception as e:
        import traceback
        print(f"PDF导出错误: {str(e)}")
        print(traceback.format_exc())

        # 返回一个简单的错误页面
        from django.http import HttpResponse
        error_html = f"""
        <html>
        <head><title>PDF导出错误</title></head>
        <body>
            <h1>PDF导出失败</h1>
            <p>错误信息: {str(e)}</p>
            <p><a href="javascript:history.back()">返回</a></p>
        </body>
        </html>
        """
        return HttpResponse(error_html)



@require_POST
def ai_content_analysis(request):
    """AI语义分析搜索内容"""
    try:
        search_query = request.POST.get('search_query', '')
        video_results = request.POST.get('video_results', '[]')

        if not search_query:
            return JsonResponse({'success': False, 'message': '搜索关键词不能为空'})

        # 解析视频结果
        try:
            import json
            video_data = json.loads(video_results) if video_results else []
        except:
            video_data = []

        # 生成AI分析提示词
        prompt = get_ai_analysis_prompt(search_query, video_data)

        # 调用AI进行语义分析
        try:
            # 优先使用DeepSeek
            system_prompt = "你是一个专业的内容分析助手，专门判断搜索内容是否与学习、教育、知识获取相关。请严格按照要求的格式回复。"
            ai_response = call_deepseek_api(system_prompt, prompt, "")

            if "无法连接" in ai_response or "调用失败" in ai_response:
                # DeepSeek失败，尝试Gemini
                try:
                    import google.generativeai as genai
                    api_key = "AIzaSyC1CWNsj_9QDinZNm-RTnu1lgHU5VbROf0"
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel('gemini-1.5-flash')
                    response = model.generate_content(f"{system_prompt}\n\n{prompt}")
                    ai_response = response.text
                except Exception as e:
                    return JsonResponse({
                        'success': False,
                        'message': f'AI分析服务暂时不可用: {str(e)}'
                    })

            # 解析AI回复
            is_learning = None
            confidence = 0.5
            reason = ai_response

            # 简单的回复解析
            if "判断结果:" in ai_response:
                lines = ai_response.split('\n')
                for line in lines:
                    if "判断结果:" in line:
                        if "是" in line:
                            is_learning = True
                        elif "否" in line:
                            is_learning = False
                    elif "置信度:" in line:
                        try:
                            confidence_str = re.search(r'[\d.]+', line)
                            if confidence_str:
                                confidence = float(confidence_str.group())
                        except:
                            pass
                    elif "理由:" in line:
                        reason = line.replace("理由:", "").strip()
            else:
                # 如果没有按格式回复，尝试从内容中推断
                if any(word in ai_response.lower() for word in ['学习', '教育', '知识', '教程', '课程']):
                    is_learning = True
                elif any(word in ai_response.lower() for word in ['娱乐', '游戏', '搞笑', '非学习']):
                    is_learning = False

            return JsonResponse({
                'success': True,
                'is_learning_related': is_learning,
                'confidence': confidence,
                'reason': reason,
                'full_response': ai_response
            })

        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'AI分析过程出错: {str(e)}'
            })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'请求处理出错: {str(e)}'
        })
