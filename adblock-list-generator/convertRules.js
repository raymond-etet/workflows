#!/usr/bin/env node

const fs = require("fs");
const https = require("https");
const http = require("http");
const path = require("path");

// --- 辅助函数 (未改变) ---

/**
 * 异步下载给定 URL 的内容。
 * @param {string} url 要下载的 URL
 * @returns {Promise<string>} 返回文件内容的 Promise
 */
function fetchUrl(url) {
  // ... (此函数未改变，为简洁省略)
  return new Promise((resolve, reject) => {
    const client = url.startsWith("https") ? https : http;
    client
      .get(url, (res) => {
        if (
          res.statusCode >= 300 &&
          res.statusCode < 400 &&
          res.headers.location
        ) {
          console.log(`  -> 重定向至: ${res.headers.location}`);
          return resolve(fetchUrl(res.headers.location));
        }
        if (res.statusCode !== 200) {
          return reject(new Error(`请求失败，状态码: ${res.statusCode}`));
        }
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => (body += chunk));
        res.on("end", () => resolve(body));
      })
      .on("error", (err) => reject(new Error(err.message)));
  });
}

/**
 * 从单行文本中提取并规范化域名。
 * @param {string} line 输入的单行文本。
 * @returns {string|null} 返回小写的域名，如果不是有效规则则返回 null。
 */
function normalizeDomain(line) {
  // ... (此函数未改变，为简洁省略)
  const raw = line.trim();
  if (
    !raw ||
    raw.startsWith("#") ||
    raw.startsWith("!") ||
    raw.startsWith("[")
  ) {
    return null;
  }
  let match;
  let domain = null;
  match = raw.match(/^(?:0\.0\.0\.0|127\.0\.0\.1|::1?)\s+([^\s#]+)/);
  if (match) {
    domain = match[1];
  }
  if (!domain) {
    match = raw.match(/^\|\|?([a-z0-9.-]+)(?:\^|\/)/i);
    if (match) {
      domain = match[1];
    }
  }
  if (!domain) {
    const cleanedLine = raw.replace(/^@?@?\|\|/, "").replace(/\^.*$/, "");
    match = cleanedLine.match(/^[a-z0-9.-]+\.[a-z]{2,}/i);
    if (match) {
      domain = match[0];
    }
  }
  if (domain) {
    return domain.split(":")[0].toLowerCase();
  }
  return null;
}

/**
 * 将重复的域名信息写入文件。
 * @param {Map<string, string[]>} domainMap 包含所有域名及其原始行的 Map
 * @param {string} filePath 输出文件路径
 * @returns {number} 返回重复集合的数量
 */
function writeDuplicatesLog(domainMap, filePath) {
  // ... (此函数未改变，为简洁省略)
  const duplicates = [];
  for (const [domain, originalLines] of domainMap.entries()) {
    if (originalLines.length > 1) {
      let entry = `# 域名: ${domain} (共发现 ${originalLines.length} 次)\n`;
      entry += originalLines.map((line) => `  - ${line}`).join("\n");
      duplicates.push(entry);
    }
  }

  if (duplicates.length > 0) {
    const header = [
      `! Title: Duplicate Domain Log`,
      `! Description: Lists all domains that appeared more than once across all sources.`,
      `! Last Updated: ${new Date().toISOString()}`,
      `! Total Duplicate Sets: ${duplicates.length}`,
      `!`,
      ``,
    ].join("\n");

    fs.writeFileSync(filePath, header + duplicates.join("\n\n"), "utf-8");
    return duplicates.length;
  }
  return 0;
}

/**
 * 根据 URL 生成一个安全的文件名。
 * @param {string} url 输入的 URL
 * @returns {string} 返回一个清理过的、适合做文件名的字符串
 */
function sanitizeUrlToFilename(url) {
  // ... (此函数未改变，为简洁省略)
  try {
    const urlObj = new URL(url);
    let safeName = (urlObj.hostname + urlObj.pathname).replace(
      /[^a-zA-Z0-9.-]/g,
      "_"
    );
    safeName = safeName.replace(/_$/, "").replace(/\.$/, "");
    return safeName.replace(/_{2,}/g, "_");
  } catch (e) {
    return `invalid_url_${Date.now()}`;
  }
}

/**
 * 主执行函数
 */
async function main() {
  // --- 自动化文件命名 ---
  const sourcesFile = "sources.txt";

  if (!fs.existsSync(sourcesFile)) {
    console.error(
      `❌ 错误: 输入源文件 "${sourcesFile}" 不存在。请在脚本同目录下创建该文件。`
    );
    process.exit(1);
  }

  const today = new Date();
  const dateSuffix =
    today.getFullYear() +
    String(today.getMonth() + 1).padStart(2, "0") +
    String(today.getDate()).padStart(2, "0");

  const outputFile = `rules.txt`;
  const duplicatesFile = `duplicates_${dateSuffix}.txt`;

  const rawSourcesDir = path.join(__dirname, "sources");
  if (!fs.existsSync(rawSourcesDir)) {
    fs.mkdirSync(rawSourcesDir, { recursive: true });
    console.log(`📁 已创建目录: ${rawSourcesDir}`);
  }
  // --- 命名结束 ---

  console.log(`\n输入文件: ${sourcesFile}`);
  console.log(`输出文件: ${outputFile}`);
  console.log(`重复日志: ${duplicatesFile}`);
  console.log(`源文件备份目录: ${rawSourcesDir}`);

  const urls = fs
    .readFileSync(sourcesFile, "utf-8")
    .split(/\r?\n/)
    .map((u) => u.trim().replace(/#.*$/, ""))
    .filter(Boolean);

  console.log(`\n🔎 找到 ${urls.length} 个规则源...`);

  const domainMappings = new Map();
  let failedSources = 0;
  // 【新增】用于统计所有源的有效规则总数（去重前）
  let totalRulesParsed = 0;

  for (const url of urls) {
    console.log(`\n⬇️  正在下载: ${url}`);
    try {
      const content = await fetchUrl(url);

      const safeFilename = `${sanitizeUrlToFilename(url)}_${dateSuffix}.txt`;
      const rawSourcePath = path.join(rawSourcesDir, safeFilename);
      fs.writeFileSync(rawSourcePath, content, "utf-8");
      console.log(`  💾 原始文件已备份至: sources/${safeFilename}`);

      const lines = content.split(/\r?\n/);
      // 【修改】使用两个独立的计数器，一个统计文件内的有效规则，一个统计新增的唯一域名
      let validRulesInFile = 0;
      let newUniqueDomainsInFile = 0;

      for (const line of lines) {
        const domain = normalizeDomain(line);
        if (domain) {
          // 只要是有效规则，就计数
          validRulesInFile++;

          const trimmedLine = line.trim();
          if (!domainMappings.has(domain)) {
            domainMappings.set(domain, []);
            // 只有当域名是第一次出现时，才计入 "新增" 数量
            newUniqueDomainsInFile++;
          }
          domainMappings.get(domain).push(trimmedLine);
        }
      }
      // 【新增】将当前文件解析出的规则数累加到总数中
      totalRulesParsed += validRulesInFile;

      // 【修改】更新日志，提供更详细的信息
      console.log(
        `  ✅ 处理完毕，找到 ${validRulesInFile} 条有效规则，新增 ${newUniqueDomainsInFile} 个唯一域名。`
      );
    } catch (err) {
      console.error(`  ❌ 下载或处理失败: ${err.message}`);
      failedSources++;
    }
  }

  if (domainMappings.size === 0) {
    console.error("\n❌ 未能成功提取任何域名，请检查源文件和网络连接。");
    process.exit(1);
  }

  const sortedDomains = [...domainMappings.keys()].sort();

  const header = [
    `! Title: Merged Adblock List`,
    `! Description: A merged and de-duplicated list from various sources.`,
    `! Last Updated: ${new Date().toISOString()}`,
    `! Domains: ${sortedDomains.length}`,
    `!`,
  ].join("\n");
  const outputContent =
    header + "\n" + sortedDomains.map((d) => `||${d}^`).join("\n") + "\n";
  fs.writeFileSync(outputFile, outputContent, "utf-8");

  const duplicateSetsCount = writeDuplicatesLog(domainMappings, duplicatesFile);

  console.log("\n----------------------------------------");
  console.log("🎉 任务完成！");
  // 【新增】在最终摘要中报告解析出的规则总数
  console.log(`   - 总计解析规则数: ${totalRulesParsed}`);
  console.log(`   - 唯一域名总数: ${sortedDomains.length}`);
  console.log(`   - 重复域名集合数: ${duplicateSetsCount}`);
  console.log(`   - 规则文件已保存至: ${outputFile}`);
  console.log(`   - 重复项日志已保存至: ${duplicatesFile}`);
  console.log(`   - 原始规则源已备份至: sources/`);
  if (failedSources > 0) {
    console.log(`   - 下载失败的源: ${failedSources}`);
  }
  console.log("----------------------------------------");
}

main().catch((err) => {
  console.error("\n🔥 发生严重错误:", err);
  process.exit(1);
});
