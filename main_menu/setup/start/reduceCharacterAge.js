
const fs = require("fs");
const charsFile = fs.readFileSync("characters.txt").toString(); // copy main_menu/setup/start/05_characters.txt from game files into this folder and rename to just characters.txt
let charsData = charsFile.replace(/(\d{4})/g, year => (Number(year)+107).toString()); // increase all year counts by 1444-1337=107
fs.writeFileSync("05_characters.txt", charsData);

