"""
内容过滤和检测工具
用于检测搜索内容是否与学习相关
"""

import os
import re
import jieba
from typing import List, Dict, Tuple, Optional
from django.conf import settings


class ContentFilter:
    """内容过滤器"""
    
    def __init__(self):
        self.keywords = {}
        self.load_keywords()
        
    def load_keywords(self):
        """加载关键词库"""
        try:
            # 关键词文件路径
            keywords_file = os.path.join(settings.BASE_DIR, 'content_filter_keywords.txt')
            
            if not os.path.exists(keywords_file):
                print(f"关键词文件不存在: {keywords_file}")
                return
                
            with open(keywords_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if ':' in line:
                            category, keywords_str = line.split(':', 1)
                            keywords_list = [kw.strip() for kw in keywords_str.split(',') if kw.strip()]
                            self.keywords[category] = keywords_list
                            
            print(f"已加载关键词库，包含 {len(self.keywords)} 个分类")
            
        except Exception as e:
            print(f"加载关键词库失败: {str(e)}")
            self.keywords = {}
    
    def segment_text(self, text: str) -> List[str]:
        """对文本进行分词"""
        if not text:
            return []
        
        # 使用jieba分词
        words = jieba.lcut(text.lower())
        
        # 过滤掉长度小于2的词和标点符号
        filtered_words = []
        for word in words:
            if len(word) >= 2 and re.match(r'^[\u4e00-\u9fa5a-zA-Z0-9]+$', word):
                filtered_words.append(word)
                
        return filtered_words
    
    def check_keywords(self, text: str) -> Dict[str, any]:
        """基于关键词库检查文本"""
        if not text or not self.keywords:
            return {'method': 'keywords', 'is_learning': None, 'confidence': 0, 'matched_words': []}
        
        # 分词
        words = self.segment_text(text)
        if not words:
            return {'method': 'keywords', 'is_learning': None, 'confidence': 0, 'matched_words': []}
        
        # 检查各类关键词
        positive_matches = []
        negative_matches = []
        
        # 检查正面关键词
        for category in ['learning_positive', 'positive_zones', 'tech_positive', 'subject_positive', 'skill_positive']:
            if category in self.keywords:
                for keyword in self.keywords[category]:
                    if keyword.lower() in text.lower() or keyword.lower() in ' '.join(words):
                        positive_matches.append((category, keyword))
        
        # 检查负面关键词
        for category in ['learning_negative', 'negative_zones', 'game_negative', 'entertainment_negative', 'daily_negative']:
            if category in self.keywords:
                for keyword in self.keywords[category]:
                    if keyword.lower() in text.lower() or keyword.lower() in ' '.join(words):
                        negative_matches.append((category, keyword))
        
        # 计算置信度
        positive_score = len(positive_matches)
        negative_score = len(negative_matches)
        
        if positive_score > 0 or negative_score > 0:
            total_score = positive_score + negative_score
            confidence = max(positive_score, negative_score) / total_score if total_score > 0 else 0
            
            if positive_score > negative_score:
                return {
                    'method': 'keywords',
                    'is_learning': True,
                    'confidence': confidence,
                    'matched_words': positive_matches,
                    'reason': f'匹配到 {positive_score} 个学习相关关键词'
                }
            else:
                return {
                    'method': 'keywords',
                    'is_learning': False,
                    'confidence': confidence,
                    'matched_words': negative_matches,
                    'reason': f'匹配到 {negative_score} 个非学习相关关键词'
                }
        
        return {'method': 'keywords', 'is_learning': None, 'confidence': 0, 'matched_words': []}
    
    def check_bilibili_zone(self, zone_name: str) -> Dict[str, any]:
        """检查B站分区是否与学习相关"""
        if not zone_name:
            return {'method': 'zone', 'is_learning': None, 'confidence': 0}
        
        zone_lower = zone_name.lower()
        
        # 明确的学习相关分区
        learning_zones = [
            '知识', '科普', '教育', '学习', '课堂', '讲座', '培训',
            '编程', '开发', '技术', '科技', '数码', '工程',
            '数学', '物理', '化学', '生物', '历史', '地理',
            '语言', '英语', '日语', '考试', '考研'
        ]
        
        # 明确的非学习分区
        non_learning_zones = [
            '游戏', '娱乐', '搞笑', '鬼畜', '音乐', '舞蹈',
            '生活', '美食', '时尚', '美妆', '动物', '宠物',
            '体育', '运动', '汽车', '旅游', '影视', '动画',
            '番剧', '电影', '电视剧', '综艺', '直播'
        ]
        
        for zone in learning_zones:
            if zone in zone_lower:
                return {
                    'method': 'zone',
                    'is_learning': True,
                    'confidence': 0.9,
                    'reason': f'属于学习相关分区: {zone_name}'
                }
        
        for zone in non_learning_zones:
            if zone in zone_lower:
                return {
                    'method': 'zone',
                    'is_learning': False,
                    'confidence': 0.9,
                    'reason': f'属于非学习相关分区: {zone_name}'
                }
        
        return {'method': 'zone', 'is_learning': None, 'confidence': 0}
    
    def analyze_content(self, search_query: str, video_results: List[Dict] = None) -> Dict[str, any]:
        """综合分析搜索内容和结果"""
        analysis_results = []
        
        # 1. 分析搜索关键词
        if search_query:
            keyword_result = self.check_keywords(search_query)
            if keyword_result['is_learning'] is not None:
                analysis_results.append({
                    'source': 'search_query',
                    'content': search_query,
                    **keyword_result
                })
        
        # 2. 分析视频结果
        if video_results:
            for i, video in enumerate(video_results[:5]):  # 只分析前5个结果
                video_analysis = []
                
                # 分析视频标题
                if video.get('title'):
                    title_result = self.check_keywords(video['title'])
                    if title_result['is_learning'] is not None:
                        video_analysis.append({
                            'source': f'video_{i}_title',
                            'content': video['title'],
                            **title_result
                        })
                
                # 分析视频分区
                if video.get('zone') or video.get('tname'):
                    zone = video.get('zone') or video.get('tname')
                    zone_result = self.check_bilibili_zone(zone)
                    if zone_result['is_learning'] is not None:
                        video_analysis.append({
                            'source': f'video_{i}_zone',
                            'content': zone,
                            **zone_result
                        })
                
                # 分析视频标签
                if video.get('tags'):
                    tags_text = ' '.join(video['tags']) if isinstance(video['tags'], list) else str(video['tags'])
                    tags_result = self.check_keywords(tags_text)
                    if tags_result['is_learning'] is not None:
                        video_analysis.append({
                            'source': f'video_{i}_tags',
                            'content': tags_text,
                            **tags_result
                        })
                
                analysis_results.extend(video_analysis)
        
        # 3. 综合判断
        if not analysis_results:
            return {
                'is_learning_related': None,
                'confidence': 0,
                'need_ai_analysis': True,
                'reason': '关键词库无法判断，需要AI语义分析',
                'details': []
            }
        
        # 计算总体倾向，给搜索关键词更高的权重
        search_query_weight = 10  # 搜索关键词权重为10
        video_content_weight = 1   # 视频内容权重为1

        learning_score = 0
        non_learning_score = 0

        for result in analysis_results:
            weight = search_query_weight if result['source'] == 'search_query' else video_content_weight

            if result['is_learning'] == True:
                learning_score += weight
            elif result['is_learning'] == False:
                non_learning_score += weight

        total_confidence = sum(r['confidence'] for r in analysis_results) / len(analysis_results)



        if learning_score > non_learning_score:
            return {
                'is_learning_related': True,
                'confidence': total_confidence,
                'need_ai_analysis': False,
                'reason': f'关键词分析显示与学习相关',
                'details': analysis_results
            }
        elif non_learning_score > learning_score:
            return {
                'is_learning_related': False,
                'confidence': total_confidence,
                'need_ai_analysis': False,
                'reason': f'关键词分析显示与学习无关',
                'details': analysis_results
            }
        else:
            return {
                'is_learning_related': None,
                'confidence': total_confidence,
                'need_ai_analysis': True,
                'reason': f'关键词分析结果不明确，需要AI语义分析',
                'details': analysis_results
            }


# 全局实例
content_filter = ContentFilter()


def analyze_search_content(search_query: str, video_results: List[Dict] = None) -> Dict[str, any]:
    """分析搜索内容是否与学习相关"""
    return content_filter.analyze_content(search_query, video_results)


def need_ai_semantic_analysis(analysis_result: Dict[str, any]) -> bool:
    """判断是否需要AI语义分析"""
    return analysis_result.get('need_ai_analysis', False)


def get_ai_analysis_prompt(search_query: str, video_results: List[Dict] = None) -> str:
    """生成AI语义分析的提示词"""
    prompt = f"""请分析以下搜索内容是否与学习、教育、知识获取相关。

搜索关键词: {search_query}

"""
    
    if video_results:
        prompt += "搜索结果中的视频信息:\n"
        for i, video in enumerate(video_results[:3], 1):
            prompt += f"{i}. 标题: {video.get('title', '未知')}\n"
            if video.get('zone') or video.get('tname'):
                prompt += f"   分区: {video.get('zone') or video.get('tname')}\n"
            if video.get('desc'):
                prompt += f"   简介: {video.get('desc', '')[:100]}...\n"
            prompt += "\n"
    
    prompt += """请判断这些内容是否与学习相关，并给出判断理由。
回复格式：
判断结果: [是/否]
置信度: [0-1之间的数值]
理由: [详细说明]"""
    
    return prompt
