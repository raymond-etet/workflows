const fs = require('fs');

const REQUIRED_ACTIVE_GROUPS = ['ai组', '币安', 'pikpak', 'Microsoft', 'Amazon'];

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

function parseProxyGroups(content) {
  const lines = content.split(/\r?\n/);
  const groups = [];
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
      currentGroup = { name: groupMatch[1].trim() };
      groups.push(currentGroup);
      continue;
    }

    if (!currentGroup) {
      continue;
    }

    const typeMatch = line.match(/^\s+type:\s*(.+)$/);
    if (typeMatch) {
      currentGroup.type = typeMatch[1].trim();
    }
  }

  return groups;
}

function findInactiveServiceGroups(content) {
  const groups = parseProxyGroups(content);
  const typeMap = new Map(groups.map((group) => [group.name, group.type]));

  return REQUIRED_ACTIVE_GROUPS.filter((name) => typeMap.get(name) === 'select');
}

function main() {
  const content = readInput();
  const emptyGroups = findEmptyProxyGroups(content);
  const inactiveServiceGroups = findInactiveServiceGroups(content);

  if (emptyGroups.length > 0) {
    console.error(`Empty proxy groups found: ${emptyGroups.join(', ')}`);
    process.exit(1);
  }

  if (inactiveServiceGroups.length > 0) {
    console.error(`Inactive service entry groups found: ${inactiveServiceGroups.join(', ')}`);
    process.exit(1);
  }

  console.log('No empty or inactive proxy groups found.');
}

main();
