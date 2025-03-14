/**
 * ClashVerge 代理规则配置生成脚本
 * MIT License ~
 * author : Phantasia https://github.com/MarchPhantasia
 * website : https://linux.do/t/topic/471428
 */

// ==================== 用户配置区（可自由修改） ====================

/**
 * 常用配置选项
 */
const CONFIG = {
    // 测试连接URL
    testUrl: "https://www.gstatic.com/generate_204",
    
    // 自动测试间隔 (秒)
    testInterval: 300,
    
    // 自动选择容差 (毫秒)
    tolerance: 20,
    
    // 负载均衡策略："round-robin" | "sticky-sessions" | "consistent-hashing"
    balanceStrategy: "sticky-sessions"
};

/**
 * 用户自定义规则（高优先级）
 * 这些规则会被放置在所有其他规则之前，确保不会被其他规则覆盖
 */
const USER_RULES = [
    "DOMAIN-SUFFIX,v2ex.com,被墙网站",
    "DOMAIN-SUFFIX,nodeseek.com,被墙网站",
    "DOMAIN-SUFFIX,mnapi.com,DIRECT",
    "DOMAIN-SUFFIX,ieee.org,DIRECT",
    "DOMAIN-SUFFIX,anrunnetwork.com,DIRECT",
    "DOMAIN-SUFFIX,apifox.com,DIRECT",
    "DOMAIN-SUFFIX,crond.dev,DIRECT",
    "IP-CIDR,223.113.52.0/22,DIRECT,no-resolve",
    // 在此添加更多自定义规则...
];

const SAVED_RULES = [
    "RULE-SET,reject,广告拦截",
    "RULE-SET,cncidr,DIRECT,no-resolve",
    "RULE-SET,direct,DIRECT",
    "GEOSITE,gfw,被墙网站",
    "GEOIP,CN,国内网站",
    "MATCH,国外网站"
]

/**
 * 高质量节点关键词列表
 * 用于筛选名称中包含这些关键词的节点作为高质量节点
 */
const HIGH_QUALITY_KEYWORDS = [
    // 线路类型关键词
    "家宽", "家庭宽带", "IEPL", "Iepl", "iepl",
    "IPLC", "iplc", "Iplc", "专线", "高速",
    
    // 节点等级关键词
    "高级", "精品", "原生", "SVIP", "svip", 
    "Svip", "VIP", "vip", "Vip", "Premium", 
    "premium",
    
    // 特殊用途关键词
    "特殊", "特殊线路", "游戏", "Game", "game"
    
    // 在此添加更多关键词...
];

/**
 * 代理规则配置
 * name: 规则名称
 * gfw: 是否被墙 (true=默认走代理, false=默认直连)
 * urls: 规则集链接，可以是单个URL或URL数组
 * payload: 自定义规则内容，设置后urls将被忽略
 * extraProxies: 额外添加到此规则组的代理，例如REJECT用于广告拦截
 */
const PROXY_RULES = [
    // 广告拦截
    { 
        name: "广告拦截", 
        gfw: false, 
        extraProxies: "REJECT", 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/AdvertisingLite/AdvertisingLite_Classical.yaml" 
    },
    
    // 自定义规则示例
    { 
        name: "linux.do", 
        gfw: false, 
        payload: "DOMAIN-SUFFIX,linux.do" 
    },
    
    // 常用网站分组
    { 
        name: "GitHub", 
        gfw: false, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/GitHub/GitHub.yaml" 
    },
    { 
        name: "YouTube", 
        gfw: true, 
        urls: [
            "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/YouTube/YouTube.yaml",
            "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/YouTubeMusic/YouTubeMusic.yaml"
        ]
    },
    { 
        name: "Google", 
        gfw: true, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Google/Google_No_Resolve.yaml" 
    },
    { 
        name: "openAi", 
        gfw: true, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/OpenAI/OpenAI_No_Resolve.yaml" 
    },
    { 
        name: "Netflix", 
        gfw: true, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Netflix/Netflix_No_Resolve.yaml" 
    },
    { 
        name: "Twitter", 
        gfw: true, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Twitter/Twitter_No_Resolve.yaml" 
    },
    { 
        name: "TikTok", 
        gfw: true, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/TikTok/TikTok_No_Resolve.yaml" 
    },
    { 
        name: "Facebook", 
        gfw: true, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Facebook/Facebook_No_Resolve.yaml" 
    },
    { 
        name: "OneDrive", 
        gfw: false, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/OneDrive/OneDrive_No_Resolve.yaml" 
    },
    { 
        name: "Microsoft", 
        gfw: false, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Microsoft/Microsoft_No_Resolve.yaml" 
    },
    { 
        name: "Steam", 
        gfw: false, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@release/rule/Clash/Steam/Steam_No_Resolve.yaml" 
    },
    { 
        name: "Cloudflare", 
        gfw: false, 
        urls: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Cloudflare/Cloudflare_No_Resolve.yaml" 
    },
    
    // 在此添加更多规则...
];

/**
 * DNS 配置
 * 可根据需要修改DNS服务器
 */
const DNS_CONFIG = {
    // 国际可信DNS (加密)
    trustDnsList: [
        "tls://8.8.8.8", "tls://1.1.1.1", "tls://9.9.9.9",
        "https://8.8.8.8/dns-query", "https://1.1.1.1/dns-query"
    ],
    
    // 默认DNS (用于解析域名服务器，必须为IP，可加密)
    defaultDNS: ["tls://1.12.12.12", "tls://223.5.5.5"],
    
    // 中国大陆DNS服务器
    cnDnsList: [
        '119.29.29.29',                    // Tencent Dnspod
        '223.5.5.5',                       // Ali DNS
        '1.12.12.12',                      // China Telecom
        "114.114.114.114",
    ],
    
    // DNS隐私保护过滤器
    fakeIpFilter: [
        "+.lan", "+.local",
        // Windows网络连接检测
        "+.msftconnecttest.com", "+.msftncsi.com",
        // QQ/微信快速登录检测
        "localhost.ptlogin2.qq.com", "localhost.sec.qq.com",
        "localhost.work.weixin.qq.com",
    ],
    
    // 指定域名使用的DNS服务器
    // 格式: "域名或geosite": DNS服务器
    nameserverPolicy: {
        "geosite:private": "system",
        "geosite:cn,steam@cn,category-games@cn,microsoft@cn,apple@cn": 'cnDnsList'
    },
    
    // 需要指定使用国外DNS的域名
    fallbackDomains: [
        "+.azure.com", "+.bing.com", "+.bingapis.com",
        "+.cloudflare.net", "+.docker.com", "+.docker.io",
        "+.facebook.com", "+.github.com", "+.githubusercontent.com",
        "+.google.com", "+.gstatic.com", "+.google.dev",
        "+.googleapis.cn", "+.googleapis.com", "+.googlevideo.com",
        "+.instagram.com", "+.meta.ai", "+.microsoft.com",
        "+.microsoftapp.net", "+.msn.com", "+.openai.com",
        "+.poe.com", "+.t.me", "+.twitter.com",
        "+.x.com", "+.youtube.com"
    ]
};

// ==================== 系统实现区（一般不需要修改） ====================

// 预编译高质量节点匹配的正则表达式
const HIGH_QUALITY_REGEX = new RegExp(HIGH_QUALITY_KEYWORDS.join("|"), "i");

// 构建DNS配置对象
const dns = buildDnsConfig(DNS_CONFIG);

// ==================== 辅助函数部分 ====================

/**
 * 构建DNS配置对象
 * @param {Object} config - DNS配置参数
 * @returns {Object} 完整的DNS配置对象
 */
function buildDnsConfig(config) {
    return {
        enable: true,
        listen: ":53",
        ipv6: true,
        "prefer-h3": true,
        "use-hosts": true,
        "use-system-hosts": true,
        "respect-rules": true,
        "enhanced-mode": "fake-ip",
        "fake-ip-range": "198.18.0.1/16",
        "fake-ip-filter": config.fakeIpFilter,
        "default-nameserver": config.defaultDNS,
        nameserver: config.trustDnsList,
        "proxy-server-nameserver": config.cnDnsList, // 直接引用以避免数组复制
        "nameserver-policy": {
            "geosite:private": "system",
            "geosite:cn,steam@cn,category-games@cn,microsoft@cn,apple@cn": config.cnDnsList,
        },
        fallback: config.trustDnsList,
        "fallback-filter": {
            geoip: true,
            "geoip-code": "CN",
            geosite: ["gfw"],
            ipcidr: ["240.0.0.0/4"],
            domain: config.fallbackDomains
        }
    };
}

/**
 * 创建规则提供器配置 - 使用对象复用优化性能
 * @param {string} url - 规则集URL
 * @returns {Object} 规则提供器配置对象
 */
function createRuleProviderUrl(url) {
    return {
        type: "http",
        interval: 86400,
        behavior: "classical",
        format: "yaml",
        url
    };
}

/**
 * 创建payload对应的规则 - 优化数组操作
 * @param {string|string[]} payload - 规则内容
 * @param {string} name - 规则名称
 * @returns {string[]} 处理后的规则列表
 */
function createPayloadRules(payload, name) {
    const payloads = Array.isArray(payload) ? payload : [payload];
    const len = payloads.length;
    const rules = new Array(len);
    // 直接调用 replace 而非 replaceAll（多数环境中效果相似且高效）
    const normalizedName = name.split(",").join("-");
    
    for (let i = 0; i < len; i++) {
        const item = payloads[i];
        const p = item.split(",");
        let insertPos = p.length;
        
        // 比较时避免转换大小写
        const last = p[p.length - 1];
        if (last === "no-resolve" || last === "NO-RESOLVE") {
            insertPos--;
        }
        
        p.splice(insertPos, 0, normalizedName);
        rules[i] = p.join(",");
    }
    
    return rules;
}

/**
 * 创建GFW（被墙）代理组
 * @param {string} name - 代理组名称
 * @param {string|string[]} addProxies - 额外代理
 * @param {string} testUrl - 测试链接
 * @returns {Object} 代理组配置
 */
function createGfwProxyGroup(name, addProxies, testUrl) {
    addProxies = addProxies ? (Array.isArray(addProxies) ? addProxies : [addProxies]) : [];
    return {
        "name": name,
        "type": "select",
        "proxies": [...addProxies, "自动选择(最低延迟)", "负载均衡", "DIRECT"],
        "include-all": true,
        "url": testUrl
    }
}

/**
 * 创建普通（非GFW）代理组
 * @param {string} name - 代理组名称
 * @param {string|string[]} addProxies - 额外代理
 * @param {string} testUrl - 测试链接
 * @returns {Object} 代理组配置
 */
function createProxyGroup(name, addProxies, testUrl) {
    addProxies = addProxies ? (Array.isArray(addProxies) ? addProxies : [addProxies]) : [];
    return {
        "name": name,
        "type": "select",
        "proxies": [...addProxies, "DIRECT", "自动选择(最低延迟)", "负载均衡"],
        "include-all": true,
        "url": testUrl
    }
}

/**
 * 筛选高质量节点 - 使用正则表达式优化性能
 * @param {Array} proxies - 所有代理节点
 * @returns {Array} 符合条件的高质量节点名称列表
 */
function filterHighQualityProxies(proxies) {
    if (!proxies || !Array.isArray(proxies)) {
        return [];
    }
    
    const result = [];
    const len = proxies.length;
    const regex = HIGH_QUALITY_REGEX; // 缓存引用
    
    for (let i = 0; i < len; i++) {
        const proxy = proxies[i];
        const proxyName = proxy.name || "";
        if (regex.test(proxyName)) {
            result.push(proxyName);
        }
    }
    
    return result;
}

/**
 * 主函数：生成完整的Clash配置
 * @param {Object} config - 输入配置
 * @returns {Object} 完整的Clash配置
 */
function main(config) {
    const { proxies } = config;
    const testUrl = CONFIG.testUrl;

    // 筛选高质量节点
    const highQualityProxies = filterHighQualityProxies(proxies);

    // 初始化规则和代理组
    const rules = USER_RULES.slice();
    const proxyGroups = [];
    const gfwProxyGroups = [];

    // 规则集通用配置
    const ruleProviderCommon = {
        type: "http",
        format: "yaml",
        interval: 86400
    };

    // 初始化规则提供器
    const ruleProviders = {
        reject: {
            ...ruleProviderCommon,
            behavior: "domain",
            url: "https://fastly.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/reject.txt",
            path: "./ruleset/loyalsoldier/reject.yaml"
        },
        cncidr: {
            ...ruleProviderCommon,
            behavior: "ipcidr",
            url: "https://fastly.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/cncidr.txt",
            path: "./ruleset/loyalsoldier/cncidr.yaml"
        },
        direct: {
            ...ruleProviderCommon,
            behavior: "domain",
            url: "https://fastly.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/direct.txt",
            path: "./ruleset/loyalsoldier/direct.yaml"
        }
    };

    const configLen = PROXY_RULES.length;
    for (let i = 0; i < configLen; i++) {
        const { name, gfw, urls, payload, extraProxies } = PROXY_RULES[i];

        // 创建代理组
        if (gfw) {
            gfwProxyGroups.push(createGfwProxyGroup(name, extraProxies, testUrl));
        } else {
            proxyGroups.push(createProxyGroup(name, extraProxies, testUrl));
        }

        // 处理规则
        if (payload) {
            rules.push(...createPayloadRules(payload, name));
        } else if (urls) {
            const urlList = Array.isArray(urls) ? urls : [urls];
            const urlLen = urlList.length;
            for (let j = 0; j < urlLen; j++) {
                const theUrl = urlList[j];
                const iName = `${name}-rule${j !== 0 ? `-${j}` : ''}`;
                ruleProviders[iName] = createRuleProviderUrl(theUrl);
                rules.push(`RULE-SET,${iName},${name}`);
            }
        }
    }

    // 构建基本代理组
    const baseProxyGroups = buildBaseProxyGroups(testUrl, highQualityProxies);

    // 构建最终配置
    return {
        mode: "rule",
        "find-process-mode": "strict",
        "global-client-fingerprint": "chrome",
        "unified-delay": true,
        "tcp-concurrent": true,
        "geox-url": {
            geoip: "https://ghgo.xyz/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip-lite.dat",
            geosite: "https://ghgo.xyz/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat",
        },
        dns,
        proxies,
        "proxy-groups": [
            ...baseProxyGroups,
            ...gfwProxyGroups,
            ...proxyGroups,
        ],
        "rule-providers": ruleProviders,
        rules: [
            ...rules,
            ...SAVED_RULES
        ]
    };
}

/**
 * 构建基本代理组
 * @param {string} testUrl - 测试URL
 * @param {Array} highQualityProxies - 高质量节点列表
 * @returns {Array} 基本代理组配置
 */
function buildBaseProxyGroups(testUrl, highQualityProxies) {
    return [
        // 基本代理组
        {
            "name": "国内网站",
            "type": "select",
            "proxies": ["DIRECT", "自动选择(最低延迟)", "负载均衡", "HighQuality"],
            "include-all": true,
            "url": "https://www.baidu.com/favicon.ico"
        },
        {
            "name": "国外网站",
            "type": "select",
            "proxies": ["DIRECT", "自动选择(最低延迟)", "负载均衡", "HighQuality"],
            "include-all": true,
            "url": "https://www.bing.com/favicon.ico"
        },
        {
            "name": "被墙网站",
            "type": "select",
            "proxies": ["自动选择(最低延迟)", "负载均衡", "DIRECT", "HighQuality"],
            "include-all": true,
            "url": testUrl
        },
        // 高质量节点组
        {
            "name": "HighQuality",
            "type": "select",
            "proxies": [
                "自动选择(最低延迟)",
                "负载均衡",
                "DIRECT",
                ...(highQualityProxies.length > 0 ? highQualityProxies : [])
            ]
        },
        // 自动选择和负载均衡
        {
            "name": "自动选择(最低延迟)",
            "type": "url-test",
            "tolerance": CONFIG.tolerance,
            "include-all": true,
            "url": testUrl,
            "interval": CONFIG.testInterval
        },
        {
            "name": "负载均衡",
            "type": "load-balance",
            "include-all": true,
            "strategy": CONFIG.balanceStrategy,
            "url": testUrl,
            "interval": CONFIG.testInterval
        },
    ];
}
