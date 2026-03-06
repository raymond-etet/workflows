const fs = require('fs');

function readInput() {
  const filePath = process.argv[2];
  if (filePath) {
    return fs.readFileSync(filePath, 'utf8');
  }
  return fs.readFileSync(0, 'utf8');
}

function findEmptyProxyGroups(content) {
  const lines = content.split(/\r?\n/);
  const emptyGroups = [];
  let inProxyGroups = false;
  let currentGroup = null;

  for (const line of lines) {
    if (!inProxyGroups) {
      if (line.trim() === 'proxy-groups:') {
        inProxyGroups = true;
      }
      continue;
    }

    if (line && !line.startsWith(' ') && !line.startsWith('-')) {
      break;
    }

    const groupMatch = line.match(/^\s*- name:\s*(.+)$/);
    if (groupMatch) {
      currentGroup = groupMatch[1].trim();
      continue;
    }

    if (currentGroup && line.trim() === 'proxies: []') {
      emptyGroups.push(currentGroup);
    }
  }

  return emptyGroups;
}

function main() {
  const content = readInput();
  const emptyGroups = findEmptyProxyGroups(content);

  if (emptyGroups.length > 0) {
    console.error(`Empty proxy groups found: ${emptyGroups.join(', ')}`);
    process.exit(1);
  }

  console.log('No empty proxy groups found.');
}

main();
