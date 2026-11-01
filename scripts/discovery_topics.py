"""Additional discovery coverage; topic matches never bypass outreach review."""

AI_TOPIC_ROOTS = (
    ('ai_robotics', 'AI robotics'),
    ('ai_computer_vision', 'computer vision tutorial'),
    ('ai_nlp', 'natural language processing tutorial'),
    ('ai_reinforcement_learning', 'reinforcement learning tutorial'),
    ('ai_safety', 'AI safety research'),
    ('ai_policy', 'AI regulation ethics'),
    ('ai_healthcare', 'AI healthcare applications'),
    ('ai_finance', 'AI finance applications'),
    ('ai_legal', 'AI legal tools'),
    ('ai_teaching', 'AI for teachers'),
    ('ai_music', 'AI music generation'),
    ('ai_voice', 'AI voice agents'),
    ('ai_translation', 'AI translation tools'),
    ('ai_local_models', 'local LLM tutorial'),
    ('ai_open_models', 'open source AI models'),
    ('ai_rag', 'retrieval augmented generation tutorial'),
    ('ai_finetuning', 'LLM fine tuning tutorial'),
    ('ai_multimodal', 'multimodal AI tutorial'),
    ('ai_evaluation', 'LLM evaluation tutorial'),
    ('ai_data', 'AI training data tutorial'),
    ('ai_hardware', 'AI hardware chips'),
    ('ai_science', 'AI scientific research'),
    ('ai_accessibility', 'AI accessibility tools'),
    ('ai_ecommerce', 'AI ecommerce tools'),
    ('ai_3d', 'AI 3D generation'),
    ('ai_animation', 'AI animation workflow'),
    ('ai_enterprise', 'enterprise AI implementation'),
    ('ai_careers', 'AI careers skills'),
)

# Full phrases avoid ambiguous standalone abbreviations such as Spanish "IA".
AI_NATIVE_ROOTS = {
    'Chinese': '人工智能', 'Japanese': '人工知能', 'Korean': '인공지능',
    'Hindi': 'कृत्रिम बुद्धिमत्ता', 'Spanish': 'inteligencia artificial',
    'Portuguese': 'inteligência artificial', 'French': 'intelligence artificielle',
    'German': 'künstliche Intelligenz', 'Indonesian': 'kecerdasan buatan',
    'Arabic': 'الذكاء الاصطناعي', 'Russian': 'искусственный интеллект',
    'Turkish': 'yapay zeka', 'Italian': 'intelligenza artificiale',
    'Dutch': 'kunstmatige intelligentie', 'Polish': 'sztuczna inteligencja',
    'Vietnamese': 'trí tuệ nhân tạo', 'Thai': 'ปัญญาประดิษฐ์',
    'Bengali': 'কৃত্রিম বুদ্ধিমত্তা', 'Urdu': 'مصنوعی ذہانت',
    'Malay': 'kecerdasan buatan', 'Filipino': 'artipisyal na intelihensiya',
    'Romanian': 'inteligență artificială', 'Persian': 'هوش مصنوعی',
    'Swahili': 'akili bandia',
}

AI_ADDITIONAL_TITLE_PATTERN = (
    r'人工智能|人工智慧|人工知能|인공지능|कृत्रिम बुद्धिमत्ता|'
    r'inteligencia artificial|inteligência artificial|intelligence artificielle|'
    r'künstliche intelligenz|kecerdasan buatan|الذكاء الاصطناعي|'
    r'искусственн\w* интеллект\w*|\bИИ\b|yapay zek[aâ]|intelligenza artificiale|'
    r'kunstmatige intelligentie|sztuczn\w* inteligenc\w*|trí tuệ nhân tạo|'
    r'ปัญญาประดิษฐ์|কৃত্রিম বুদ্ধিমত্তা|مصنوعی ذہانت|'
    r'artipisyal na intelihensiya|inteligență artificială|هوش مصنوعی|akili bandia|'
    r'\b(?:computer vision|natural language processing|reinforcement learning|'
    r'retrieval[- ]augmented generation|prompt engineering)\b'
)

CREATOR_TOPIC_ROOTS = (
    ('creator_video_editing', 'video editing tutorial'),
    ('creator_premiere', 'Adobe Premiere Pro tutorial'),
    ('creator_davinci', 'DaVinci Resolve tutorial'),
    ('creator_final_cut', 'Final Cut Pro tutorial'),
    ('creator_capcut', 'CapCut editing tutorial'),
    ('creator_after_effects', 'After Effects motion graphics'),
    ('creator_filmora', 'Filmora editing tutorial'),
    ('creator_ae', 'AE tutorial'),
    ('creator_blender', 'Blender tutorial'),
    ('creator_cinema4d', 'Cinema 4D animation tutorial'),
    ('creator_davinci_color', 'DaVinci Resolve color grading'),
    ('creator_obs', 'OBS Studio tutorial'),
    ('creator_canva', 'Canva video tutorial'),
    ('creator_descript', 'Descript editing tutorial'),
    ('creator_vegas', 'Vegas Pro editing tutorial'),
    ('creator_alight', 'Alight Motion tutorial'),
    ('creator_vn', 'VN video editor tutorial'),
    ('creator_jianying', '剪映 教程'),
    ('creator_ae_zh', 'AE 特效 教程'),
    ('creator_workflow', 'content creation workflow'),
    ('creator_tools', 'content creator tools review'),
    ('creator_youtube', 'YouTube creator strategy'),
    ('creator_short_video', 'short form video editing'),
    ('creator_podcast', 'video podcast editing workflow'),
    ('creator_zh', '视频剪辑 教程'),
    ('creator_zh', '自媒体 内容创作 工具'),
    ('creator_ja', '動画編集 チュートリアル'),
    ('creator_ko', '영상 편집 튜토리얼'),
    ('creator_es', 'edición de video tutorial'),
    ('creator_pt', 'edição de vídeo tutorial'),
    ('creator_fr', 'montage vidéo tutoriel'),
    ('creator_de', 'Videobearbeitung Anleitung'),
)

CREATOR_TITLE_PATTERN = (
    r'\b(?:video edit(?:ing|or)|premiere pro|davinci resolve|final cut pro|capcut|'
    r'after effects|ae (?:tutorial|edit(?:ing)?|effects?)|blender|cinema 4d|obs studio|'
    r'canva|descript|vegas pro|alight motion|vn video editor|filmora|motion graphics|content creat(?:ion|or)|'
    r'youtube (?:creator|strategy|growth)|podcast editing)\b|'
    r'剪映|\bAE\s*(?:教程|特效|动画)|视频剪辑|影片剪輯|视频编辑|影片編輯|自媒体|自媒體|内容创作|內容創作|'
    r'動画編集|영상\s*편집|edición de v[ií]deo|edição de v[ií]deo|'
    r'montage vid[eé]o|videobearbeitung'
)

KNOWLEDGE_TOPIC_ROOTS = (
    ('knowledge_science', 'science explained'),
    ('knowledge_physics', 'physics explained'),
    ('knowledge_biology', 'biology explained'),
    ('knowledge_chemistry', 'chemistry explained'),
    ('knowledge_space', 'astronomy explained'),
    ('knowledge_math', 'mathematics explained'),
    ('knowledge_history', 'history explained'),
    ('knowledge_geography', 'geography explained'),
    ('knowledge_economics', 'economics explained'),
    ('knowledge_finance', 'personal finance explained'),
    ('knowledge_technology', 'technology explained'),
    ('knowledge_engineering', 'engineering explained'),
    ('knowledge_psychology', 'psychology explained'),
    ('knowledge_business', 'business case study explained'),
    ('knowledge_talking_head', 'talking head educational video'),
    ('knowledge_video_essay', 'educational video essay'),
    ('knowledge_zh', '科普 知识 解说'),
    ('knowledge_zh', '口播 知识分享'),
    ('knowledge_zh', '历史 讲解'),
    ('knowledge_ja', '科学 解説'),
    ('knowledge_ko', '과학 설명'),
    ('knowledge_es', 'divulgación científica'),
    ('knowledge_pt', 'divulgação científica'),
    ('knowledge_fr', 'vulgarisation scientifique'),
    ('knowledge_de', 'Wissenschaft erklärt'),
    ('knowledge_hi', 'विज्ञान समझें'),
    ('knowledge_ar', 'تبسيط العلوم'),
)

KNOWLEDGE_TITLE_PATTERN = (
    r'\b(?:science|scientific|physics|biology|chemistry|astronomy|mathematics|'
    r'history|geography|economics|finance|technology|engineering|psychology|'
    r'explained|explainer|how (?:does|do|it works)|why|talking head|video essay)\b|'
    r'科普|科学|科學|知识|知識|口播|解说|解說|讲解|講解|历史|歷史|解説|'
    r'과학|설명|divulgaci[oó]n|divulgaç[aã]o|vulgarisation|wissenschaft|'
    r'विज्ञान|تبسيط العلوم'
)
