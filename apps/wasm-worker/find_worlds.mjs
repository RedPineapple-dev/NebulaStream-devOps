import fs from 'fs';
import path from 'path';

function findWit(dir) {
  if (!fs.existsSync(dir)) return;
  for (const f of fs.readdirSync(dir, {withFileTypes: true})) {
    const full = path.join(dir, f.name);
    if (f.isDirectory()) findWit(full);
    else if (f.name.endsWith('.wit')) {
      const content = fs.readFileSync(full, 'utf8');
      const worlds = [...content.matchAll(/^world\s+(\S+)/gm)].map(m => m[1]);
      if (worlds.length) console.log(full + ':', worlds);
    }
  }
}
findWit('node_modules/@fermyon/spin-sdk');
findWit('node_modules/@spinframework/build-tools');
