// Validate a supplied embeddable face itself, rather than trusting its filename.
import {createRequire} from 'node:module';import path from 'node:path';
const [runtime,font,postscript]=process.argv.slice(2);
const require=createRequire(path.join(runtime,'package.json'));
const face=require('fontkit').openSync(font);
if(face.postscriptName!==postscript)throw Error('可移植字体与锁定的PostScript名称不符');
let text='';for await (const chunk of process.stdin)text+=chunk;
const missing=[...new Set([...text].filter(c=>!face.hasGlyphForCodePoint(c.codePointAt(0))))];
if(missing.length)throw Error('可移植字体缺字：'+missing.join(''));
