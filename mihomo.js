/*
Clash Party / mihomo-party 覆写脚本（JavaScript）
https://linux.do/t/topic/1052124/5
遵循文档：https://clashparty.org/docs/guide/override/javascript
支持开关（通过 config.__options 传入，非官方约定）
- loadbalance: 地区分组 load-balance（默认 false 使用 url-test）
- ipv6/fakeip/full/keepalive 与原版一致
*/
function main(config) {
  config = config || {};
  const proxies = Array.isArray(config.proxies) ? config.proxies : [];
  const options = config.__options || {};
  const parseBool = (v) => (typeof v === 'boolean' ? v : typeof v === 'string' ? v.toLowerCase() === 'true' || v === '1' : false);
  const LOAD_BALANCE = parseBool(options.loadbalance) || false;
  const IPV6 = parseBool(options.ipv6) || false;
  const FULL = parseBool(options.full) || false;
  const KEEPALIVE = parseBool(options.keepalive) || false;
  const FAKEIP = parseBool(options.fakeip) || false;
  // 测速参数
  const TEST_URL = 'https://www.gstatic.com/generate_204';
  const TEST_INTERVAL = 300;
  const TEST_TOLERANCE = 50;
  const allNodeNames = (proxies || []).map((p) => p?.name).filter(Boolean);
  // 地区匹配（与 ini 对齐，包含表情命名）
  const REGION_PATTERNS = {
    '🇭🇰 香港节点': /(港|HK|Hong\s*Kong|HongKong|hongkong|深港)/i,
    '🇺🇸 美国节点': /(美|波特兰|达拉斯|俄勒冈|凤凰城|费利蒙|硅谷|拉斯维加斯|洛杉矶|圣何塞|圣克拉拉|西雅图|芝加哥|US|United\s*States|UnitedStates)/i,
    '🇯🇵 日本节点': /(日本|川日|东京|大阪|泉日|埼玉|沪日|深日|\bJP\b|Japan|🇯🇵)/i,
    '🇸🇬 新加坡节点': /(新加坡|坡|狮城|\bSG\b|Singapore)/i,
    '🇼🇸 台湾节点': /(台|新北|彰化|\bTW\b|Taiwan|台灣|臺灣)/i,
    '🇰🇷 韩国节点': /(KR|Korea|KOR|首尔|韩|韓|Korea)/i,
    '🇨🇦 加拿大节点': /(加拿大|Canada|渥太华|温哥华|卡尔加里)/i,
    '🇬🇧 英国节点': /(英国|Britain|United\s*Kingdom|England|伦敦)/i,
    '🇫🇷 法国节点': /(法国|France|巴黎)/i,
    '🇩🇪 德国节点': /(德国|Germany|柏林|法兰克福)/i,
    '🇳🇱 荷兰节点': /(荷兰|Netherlands|阿姆斯特丹)/i,
    '🇹🇷 土耳其节点': /(土耳其|Turkey|Türkiye)/i,
    '🏠 家宽节点': /(家宽|家庭宽带|住宅)/i,
  };
  const REGION_KEYWORD_UNION = new RegExp(
    Object.values(REGION_PATTERNS).map((re) => re.source).join('|'),
    'i'
  );
  const regionGroups = Object.entries(REGION_PATTERNS).map(([name, re]) => ({
    name,
    type: LOAD_BALANCE ? 'load-balance' : 'url-test',
    ...(LOAD_BALANCE
      ? { strategy: 'round-robin' }
      : { url: TEST_URL, interval: TEST_INTERVAL, tolerance: TEST_TOLERANCE }),
    proxies: allNodeNames.filter((n) => re.test(n)),
  }));
  const othersGroup = {
    name: '🌐 其他地区',
    type: 'url-test',
    url: TEST_URL,
    interval: TEST_INTERVAL,
    tolerance: TEST_TOLERANCE,
    proxies: allNodeNames.filter((n) => !REGION_KEYWORD_UNION.test(n)),
  };
  const autoSelectGroup = {
    name: '♻️ 自动选择',
    type: 'url-test',
    url: TEST_URL,
    interval: TEST_INTERVAL,
    tolerance: TEST_TOLERANCE,
    proxies: allNodeNames,
  };
  const manualSelectGroup = {
    name: '🚀 手动选择',
    type: 'select',
    proxies: [
      '♻️ 自动选择',
      ...regionGroups.map((g) => g.name),
      '🏠 家宽节点',
      '🌐 其他地区',
      ...allNodeNames,
    ],
  };
  const directGroup = { name: '🎯 全球直连', type: 'select', proxies: ['DIRECT'] };
  const nonStdPortGroup = { name: '🔀 非标端口', type: 'select', proxies: ['🎯 全球直连', '🚀 手动选择'] };
  const fallbackGroup = { name: '🐟 漏网之鱼', type: 'select', proxies: ['🚀 手动选择', '🎯 全球直连'] };
  const followRuleGroup = { name: '🐟 遵循规则', type: 'select', proxies: ['🐟 漏网之鱼'] };
  // 功能策略组（与 ini 同名）
  const FEATURE_GROUPS = [
    { name: '💬 即时通讯' },
    { name: '🌐 社交媒体' },
    { name: '📞 Talkatone', extra: ['🎯 全球直连'] },
    { name: '🚀 GitHub', extra: ['🎯 全球直连'] },
    { name: '🤖 ChatGPT' },
    { name: '🤖 Copilot', extra: ['🎯 全球直连'] },
    { name: '🤖 AI服务' },
    { name: '🎶 TikTok', extra: ['🎯 全球直连'] },
    { name: '📹 YouTube' },
    { name: '🎥 Netflix' },
    { name: '🎥 DisneyPlus' },
    { name: '🎥 HBO', extra: ['🎯 全球直连'] },
    { name: '🎥 PrimeVideo' },
    { name: '🎥 AppleTV+' },
    { name: '🍎 苹果服务', extra: ['🎯 全球直连'] },
    { name: 'Ⓜ️ 微软服务', extra: ['🎯 全球直连'] },
    { name: '📢 谷歌FCM', extra: ['🎯 全球直连'] },
    { name: '🇬 谷歌服务' },
    { name: '💾 OneDrive' },
    { name: '🎻 Spotify' },
    { name: '📺 Bahamut' },
    { name: '🎥 Emby' },
    { name: '🎮 Steam' },
    { name: '🎮 游戏平台' },
    { name: '🌎 国外媒体' },
    { name: '⏬ PT站点', extra: ['🎯 全球直连'] },
    { name: '💳 PayPal', extra: ['🎯 全球直连'] },
    { name: '🛒 国外电商' },
    { name: '🚀 测速工具', extra: ['🎯 全球直连'] },
  ];
  const featureGroups = FEATURE_GROUPS.map(({ name, extra = [] }) => ({
    name,
    type: 'select',
    proxies: [
      ...regionGroups.map((g) => g.name),
      '🏠 家宽节点',
      '🌐 其他地区',
      '🚀 手动选择',
      '♻️ 自动选择',
      ...extra,
    ],
  }));
  config['proxy-groups'] = [
    manualSelectGroup,
    autoSelectGroup,
    ...featureGroups,
    ...regionGroups,
    othersGroup,
    directGroup,
    nonStdPortGroup,
    fallbackGroup,
    followRuleGroup,
  ];
  // 远程规则集（Aethersailor，classical）
  config['rule-providers'] = {
    Custom_Direct_Classical: {
      type: 'http', behavior: 'classical', path: 'ruleset/Custom_Direct_Classical.yaml',
      url: 'https://testingcf.jsdelivr.net/gh/Aethersailor/Custom_OpenClash_Rules@main/rule/Custom_Direct_Classical.yaml', interval: 28800,
    },
    Custom_Proxy_Classical: {
      type: 'http', behavior: 'classical', path: 'ruleset/Custom_Proxy_Classical.yaml',
      url: 'https://testingcf.jsdelivr.net/gh/Aethersailor/Custom_OpenClash_Rules@main/rule/Custom_Proxy_Classical.yaml', interval: 28800,
    },
    Steam_CDN_Classical: {
      type: 'http', behavior: 'classical', path: 'ruleset/Steam_CDN_Classical.yaml',
      url: 'https://testingcf.jsdelivr.net/gh/Aethersailor/Custom_OpenClash_Rules@main/rule/Steam_CDN_Classical.yaml', interval: 2880,
    },
    Custom_Port_Direct: {
      type: 'http', behavior: 'classical', path: 'ruleset/Custom_Port_Direct.yaml',
      url: 'https://testingcf.jsdelivr.net/gh/Aethersailor/Custom_OpenClash_Rules@main/rule/Custom_Port_Direct.yaml', interval: 28800,
    },
  };
  // 规则顺序与 ini 一致（GEOSITE/GEOIP）
  const R = [];
  R.push('GEOSITE,private,🎯 全球直连');
  R.push('GEOIP,private,🎯 全球直连,no-resolve');
  R.push('RULE-SET,Custom_Direct_Classical,🎯 全球直连');
  R.push('RULE-SET,Custom_Proxy_Classical,🚀 手动选择');
  R.push('GEOSITE,google-cn,🎯 全球直连');
  R.push('GEOSITE,category-games@cn,🎯 全球直连');
  R.push('RULE-SET,Steam_CDN_Classical,🎯 全球直连');
  R.push('GEOSITE,category-game-platforms-download,🎯 全球直连');
  R.push('GEOSITE,category-public-tracker,🎯 全球直连');
  R.push('GEOSITE,category-communication,💬 即时通讯');
  R.push('GEOSITE,category-social-media-!cn,🌐 社交媒体');
  R.push('GEOSITE,talkatone,📞 Talkatone');
  R.push('GEOSITE,openai,🤖 ChatGPT');
  R.push('GEOSITE,onedrive,💾 OneDrive');
  R.push('GEOSITE,bing,🤖 Copilot');
  R.push('GEOSITE,category-ai-!cn,🤖 AI服务');
  R.push('GEOSITE,github,🚀 GitHub');
  R.push('GEOSITE,category-speedtest,🚀 测速工具');
  R.push('GEOSITE,steam,🎮 Steam');
  R.push('GEOSITE,youtube,📹 YouTube');
  R.push('GEOSITE,apple-tvplus,🎥 AppleTV+');
  R.push('GEOSITE,apple,🍎 苹果服务');
  R.push('GEOSITE,microsoft,Ⓜ️ 微软服务');
  R.push('GEOSITE,googlefcm,📢 谷歌FCM');
  R.push('GEOSITE,google,🇬 谷歌服务');
  R.push('GEOSITE,tiktok,🎶 TikTok');
  R.push('GEOSITE,netflix,🎥 Netflix');
  R.push('GEOSITE,disney,🎥 DisneyPlus');
  R.push('GEOSITE,hbo,🎥 HBO');
  R.push('GEOSITE,primevideo,🎥 PrimeVideo');
  R.push('GEOSITE,category-emby,🎥 Emby');
  R.push('GEOSITE,spotify,🎻 Spotify');
  R.push('GEOSITE,bahamut,📺 Bahamut');
  R.push('GEOSITE,category-games,🎮 游戏平台');
  R.push('GEOSITE,category-entertainment,🌎 国外媒体');
  R.push('GEOSITE,category-pt,⏬ PT站点');
  R.push('GEOSITE,paypal,💳 PayPal');
  R.push('GEOSITE,category-ecommerce,🛒 国外电商');
  R.push('GEOSITE,gfw,🚀 手动选择');
  R.push('GEOIP,telegram,💬 即时通讯,no-resolve');
  R.push('GEOIP,twitter,🌐 社交媒体,no-resolve');
  R.push('GEOIP,facebook,🌐 社交媒体,no-resolve');
  R.push('GEOIP,google,🇬 谷歌服务,no-resolve');
  R.push('GEOIP,netflix,🎥 Netflix,no-resolve');
  R.push('GEOSITE,cn,🎯 全球直连');
  R.push('GEOIP,cn,🎯 全球直连,no-resolve');
  R.push('RULE-SET,Custom_Port_Direct,🔀 非标端口');
  R.push('MATCH,🐟 漏网之鱼');
  config.rules = R;
  // 嗅探
  config.sniffer = {
    sniff: { TLS: { ports: [443, 8443] }, HTTP: { ports: [80, 8080, 8880] }, QUIC: { ports: [443, 8443] } },
    'override-destination': false,
    enable: true,
    'force-dns-mapping': true,
    'skip-domain': ['Mijia Cloud', 'dlg.io.mi.com', '+.push.apple.com'],
  };
  // DNS / geox
  const dnsBase = {
    enable: true,
    ipv6: IPV6,
    'prefer-h3': true,
    'default-nameserver': ['119.29.29.29', '223.5.5.5'],
    nameserver: ['system', '223.5.5.5', '119.29.29.29', '180.184.1.1'],
    fallback: ['quic://dns0.eu', 'https://dns.cloudflare.com/dns-query', 'https://dns.sb/dns-query', 'tcp://208.67.222.222', 'tcp://8.26.56.2'],
    'proxy-server-nameserver': ['quic://223.5.5.5', 'tls://dot.pub'],
  };
  config.dns = FAKEIP
    ? { ...dnsBase, 'enhanced-mode': 'fake-ip', 'fake-ip-filter': ['geosite:private', 'geosite:connectivity-check', 'geosite:cn', 'Mijia Cloud', 'dig.io.mi.com', 'localhost.ptlogin2.qq.com', '*.icloud.com', '*.stun.*.*', '*.stun.*.*.*'] }
    : { ...dnsBase, 'enhanced-mode': 'redir-host' };
  config['geodata-mode'] = true;
  config['geox-url'] = {
    geoip: 'https://cdn.jsdelivr.net/gh/Loyalsoldier/v2ray-rules-dat@release/geoip.dat',
    geosite: 'https://cdn.jsdelivr.net/gh/Loyalsoldier/v2ray-rules-dat@release/geosite.dat',
    mmdb: 'https://cdn.jsdelivr.net/gh/Loyalsoldier/geoip@release/Country.mmdb',
    asn: 'https://cdn.jsdelivr.net/gh/Loyalsoldier/geoip@release/GeoLite2-ASN.mmdb',
  };
  if (FULL) {
    Object.assign(config, {
      'mixed-port': 7890,
      'redir-port': 7892,
      'tproxy-port': 7893,
      'routing-mark': 7894,
      'allow-lan': true,
      ipv6: IPV6,
      mode: 'rule',
      'unified-delay': true,
      'tcp-concurrent': true,
      'find-process-mode': 'off',
      'log-level': 'info',
      'geodata-loader': 'standard',
      'external-controller': ':9999',
      'disable-keep-alive': !KEEPALIVE,
      profile: { 'store-selected': true },
    });
  }
  return config;
} 这个JS脚本里可以加入自定义IP规则吗？
