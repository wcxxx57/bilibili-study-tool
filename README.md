#  b站学习助手项目说明

wcx大一暑课大作业，一星期和ai密切合作结果:handshake:，交完作业后会完善本说明文档并研究如何​上线​！:wink:

## :sparkles:项目概述

b站学习助手网站基于**Django框架**开发，旨在帮助利用bilibili进行网课学习的用户将Bilibili从一个娱乐平台转变为一个**结构化的学习工具**。

主要功能：**搜索视频**（支持关键词/bv号/视频链接搜索，搜索与学习不相关内容时会智能提示）、**课程管理**（分集观看进度管理等）、**计划管理**（制定计划、学习记录等）、**ai助手**（配置了Google Gemini和DeepSeek两种模型）。

用户可以通过本站搜索B站上的视频教程，将其添加为个人课程，制定详细的学习计划，追踪课程观看进度，并利用AI助手获得学习建议和支持~

项目宣传视频与功能展示：[【震撼发布！】全b站大学的学生都不该错过！_哔哩哔哩_bilibili](https://www.bilibili.com/video/BV1QtgeztEyc/?spm_id_from=333.1387.homepage.video_card.click&vd_source=026d55a126174775fa14961a149ee1c5)

## :clipboard:项目结构

```
├── biliTool/             # Django项目主配置目录
│   ├── settings.py       # 项目设置 (数据库, 静态文件, etc.)
│   ├── urls.py           # 项目主路由
│   └── ...
├── bilistudy/            # 核心应用目录
│   ├── __init__.py
│   ├── admin.py          # Django后台管理配置
│   ├── apps.py           # 应用配置
│   ├── content_filter.py # 内容审查和AI分析逻辑
│   ├── models.py         # 数据库模型 (核心)
│   ├── urls.py           # bilistudy应用的路由配置
│   ├── views.py          # 视图函数 (核心业务逻辑)
│   ├── migrations/       # 数据库迁移文件
│   └── templatetags/     # 自定义模板标签
├── templates/            # HTML模板文件
│   └── bilistudy/
├── static/               # CSS, JavaScript, 图片等静态文件
├── db.sqlite3            # SQLite数据库文件
├── manage.py             # Django项目管理脚本
├── requirements.txt      # Python依赖包
└── README.md             # 本文档
```

### 文件和目录用途

*   `biliTool/`: Django项目的主文件夹，负责整个项目的配置。
*   `bilistudy/`: 项目的核心应用，包含了几乎所有的业务逻辑。
    *   `views.py`: **最核心的文件**，包含了项目中几乎所有的后端逻辑，如视频搜索、用户认证、课程管理、AI交互等。
    *   `models.py`: 定义了所有的**数据表结构**，是项目数据存储的基础。
    *   `urls.py`: 定义了`bilistudy`应用的所有**URL路由**，将URL路径映射到`views.py`中的具体函数。
    *   `content_filter.py`: 实现了基于关键词和AI的智能内容分析功能，用于判断视频内容是否与学习相关。
*   `templates/bilistudy/`: 存放所有前端页面的HTML文件。
*   `static/`: 存放CSS样式、JavaScript脚本等。
*   `content_filter_keywords.txt`: 存储了内容过滤功能使用的关键词列表，是`content_filter.py`的依赖。

## :arrow_forward:核心功能实现方案

### 3.1. 视频搜索与导入

此功能允许用户通过关键词、BV号或视频链接搜索B站视频。

*   **后端实现**:
    *   **视图函数**: `search_videos` 函数(位于 `bilistudy/views.py`)
    *   **BV号提取**: `extract_bvid_from_input` 函数通过**正则表达式**智能用户输入中提取BV号。
    *   **API调用**:
        1.  如果输入是BV号/链接，优先调用`https://api.bilibili.com/x/web-interface/view`接口获取单个视频的详细信息。
        2.  如果是关键词搜索，则调用`https://api.bilibili.com/x/web-interface/search/type`接口进行搜索。
    *   **数据处理**: 获取API返回的JSON数据后，后端会进行清洗（如去除HTML标签）、排序，并将结果渲染到前端页面。如果数据库中已有该视频信息，则直接从数据库读取，避免重复请求。
    *   **内容审查**: `analyze_search_content` 函数会在搜索时被调用，对搜索词和结果进行初步分析，判断其是否为非学习内容，并给出提醒。

*   **前端交互**:
    *   用户在首页或搜索结果页的搜索框输入内容。
    *   通过AJAX或表单提交，将请求发送到后端的`/search/`路由。
    *   前端页面(`search_results.html`)接收并展示视频列表。

### 3.2. 我的课程与学习进度

用户可以将搜索到的视频添加为“我的课程”，并跟踪每一集的学习进度。

*   **后端实现**:
    *   **数据模型**: `UserCourse` (存储用户和视频的关联), `LearningProgress` (存储每一集的完成状态)。
    *   **核心视图函数（都在`bilistudy/views.py`中)**:
        *   `add_to_course_list`: 处理添加课程的请求。当用户添加一个视频时，会为该视频的所有分集创建对应的`LearningProgress`记录。
        *   `course_list`: 展示用户的所有课程及总体学习进度。
        *   `course_detail`: 显示单个课程的详细分集列表和各自的完成状态。
        *   `update_progress`, `batch_update_progress`: 通过AJAX请求，更新单个或多个分集的完成状态 (`is_completed`字段)。

*   **前端交互**:
    *   在视频详情或搜索结果页，点击“添加到课程列表”按钮。
    *   在课程详情页，通过点击复选框来标记/取消标记分集的完成状态，该操作会触发AJAX请求到后端更新进度。

### 3.3. 学习计划管理

用户可以为已添加的课程创建学习计划，设定目标并记录每日学习情况。

*   **后端实现**:
    *   **数据模型**: `StudyPlan` (存储计划的总体信息，如总天数、每日时长), `DailyStudyRecord` (记录每日的学习时长和笔记)。
    *   **核心视图函数（都在`bilistudy/views.py`中)**:
        *   `create_study_plan`: 创建一个新的学习计划，与一个`UserCourse`关联。
        *   `plan_detail`: 展示计划的详细信息，包括日历视图、每日学习记录和进度统计。
        *   `update_daily_record`: 用户在此更新某一天实际的学习时长、笔记以及当天完成的分集。
        *   `export_plan_pdf`: 使用`reportlab`库生成一份详细的**学习报告PDF文件**，包含图表和数据分析。

*   **前端交互**:
    *   在课程详情页创建学习计划。
    *   在计划详情页，用户可以填写每日学习表单，记录当天的学习情况。进度条和图表会动态展示学习成果。

### 3.4. AI 助手

提供一个交互式聊天界面，用户可以获取学习建议、制定计划或分析进度。

*   **后端实现**:
    *   **核心视图**: `ai_chat`
    *   **提示词**: `get_system_prompt` 函数根据用户的聊天类型（通用、学习计划、进度分析）和选择的AI模型，动态生成不同的系统提示词(System Prompt)。这使得AI的回答更加专业和有针对性。
    *   **多模型支持**:
        *   `call_deepseek_api`: 封装了对DeepSeek API的调用逻辑。
        *   `ai_chat`中也包含了对Google Gemini API的直接调用逻辑。
    *   **上下文记忆**: `ChatHistory`模型用于存储对话历史。在每次请求时，后端会加载最近的几条对话记录并加入到Prompt中，实现多轮对话。
    *   **API密钥**: API密钥硬编码在`views.py`中，这是一个安全风险，建议后续修改为从环境变量或配置文件中读取。

*   **前端交互**:
    *   用户在AI助手页面输入问题，选择聊天模式（如“帮我制定计划”）和AI模型。
    *   通过AJAX将请求发送到`/ai-chat/`。
    *   前端接收到流式或一次性的AI回复，并以打字机效果展示。

## :bar_chart:数据库

项目使用**Django ORM**进行数据库操作，在`settings.py`中构建了与mysql数据库的连接。所有数据模型定义在`bilistudy/models.py`。

### 视频学习核心数据表

#### `bilistudy_bilivideo`

存储从B站获取的视频元数据（视频信息）

*   `bvid` : **视频的唯一标识，主键**。
*   `title` : 视频标题。
*   `cover` (URL): 封面图片链接。
*   `author` : UP主名称。
*   `pub_date`(date)：发布日期。
*   `play_count`：播放量。
*   `like_count`：点赞数。
*   `description` : 视频简介。

#### `bilistudy_videoepisode`

存储从B站获取的**视频分集**信息

*   `video` (关联 `biliVideo`的**外键**)：对应的主视频。
*   `cid` : **分集的唯一ID**。
*   `title` : 分集标题。
*   `duration` : 分集时长（秒）。
*   `order` : 分集顺序。

#### `bilistudy_usercourse`

**用户和课程的关联表**，代表某用户添加了某课程。

*   `user` (关联 `auth_user`的外键): 关联到用户。（ `auth_user`是django自带的存储注册的用户信息的表）
*   `video` ( 关联`biliVideo`): 关联到视频。
*   `custom_title` : 用户自定义的课程名称。
*   `add_time` : 用户添加该课程的时间。

#### `bilistudy_learningprogress`

记录每个用户对**每个分集**的学习进度，关联用户的课程与分集信息。

*   `user_course` (关联 `UserCourse`的外键): 关联到用户的课程。
*   `episode` (关联 `VideoEpisode`的外键): 关联到具体分集。
*   `is_completed` (Bool): 标记该分集**是否已学习完成**。
*   `completed_at` (Datetime): **完成时间**。

#### `bilistudy_studyplan`

存储每个用户学习计划的核心信息。

*   `user` (关联 `auth_user`的外键): 计划所属用户。
*   `user_course` (OneToOneField to `UserCourse`): **一对一关联**到某个课程，一个课程只能有一个计划。
*   `total_days` : 计划总天数。
*   `daily_minutes` : 每日计划学习时长。
*   `start_date` : 计划开始日期。

#### `bilistudy_dailystudyrecord`

每日学习打卡记录。

*   `study_plan` (关联 `StudyPlan`的外键): 关联到具体的学习计划。
*   `study_date` : 学习日期。
*   `study_minutes` : 当天实际学习的时长。
*   `notes` : 当天的学习备忘。

#### 其他表

*   `auth_user`: Django内置的用户表。
*   `bilistudy_emailverification`: 存储用于注册和密码重置的邮箱验证码。
*   `bilistudy_chathistory`: 存储AI助手的聊天记录。
*   `bilistudy_userpreference`: 存储用户的个性化设置，如主题、是否开启学习提醒等。

### 数据更新流程

*   **搜索时**: 如果视频不在`BiliVideo`表中，则通过API获取信息并创建新记录。
*   **添加课程时**: 创建一条`UserCourse`记录，并为该课程的**所有分集**批量创建`LearningProgress`记录（`is_completed`默认为`False`）。
*   **更新进度时**: 用户在前端勾选复选框，AJAX请求触发后端更新对应`LearningProgress`记录的`is_completed`字段。
*   **记录学习时**: 用户提交每日学习表单，后端创建或更新一条`DailyStudyRecord`记录。

## :pushpin:项目使用的第三方API

- **Bilibili API**: 获取视频数据。

*   **Google Gemini API**: 提供AI聊天和分析能力。
*   **DeepSeek API**: 提供AI聊天和分析能力。 

## :gear:项目所需配置

- gemini和deepseek的调用需要申请api key，请在代码对应的部分修改为自己的api key

- 邮箱发送验证码功能需要配置自己的邮箱及端口密码（不是邮箱密码）

- 需创建自己的mysql数据库并设置账户（`root`）和密码

  申请好以上配置后添加如下格式的`.env`配置文件填写相关信息，或应编码到代码相关位置（不推荐）。

  ```
  # Django SECRET_KEY
  SECRET_KEY= ''
  
  # Database settings
  DB_NAME=bilicourse(先在mysql创建一个这个名字的数据库)
  DB_USER=root
  DB_PASSWORD=''
  
  # Email settings（我用过的是163邮箱）
  EMAIL_HOST=smtp.163.com
  EMAIL_PORT=25
  EMAIL_USE_TLS=True
  EMAIL_HOST_USER=xxxxx@163.com
  EMAIL_HOST_PASSWORD=xxxxx（到163邮箱网址获取，不是个人邮箱密码）
  
  # Gemini API配置
  GEMINI_API_KEY= ''（你的gemini api key）
  
  # DeepSeek API配置
  DEEPSEEK_API_KEY=''（你的deepseek api key）
  ```

