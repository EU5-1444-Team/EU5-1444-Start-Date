const { log } = require("console");
const fs = require("fs");
const fileBlacklist = [
    /\.github/,
    /\.git/,
    /media/,
    /.*\.js/,
    /tools/,
    /.*\.md/
];
const deployFolder = "../1444StartReadyForPublish";
const gameVersion = "1.0.9"

if (fs.existsSync(deployFolder)) fs.rmSync(deployFolder, { recursive: true, force: true });
fs.mkdirSync(deployFolder);

const fileList = fs.readdirSync("./");
for (const file of fileList) {
    let isBlacklisted = false;
    for (const blackItem of fileBlacklist) {
        if (blackItem.test(file)) {

            isBlacklisted = true;
        }
    }
    if (!isBlacklisted) fs.cpSync(file, deployFolder + "/" + file, { recursive: true });
}

const metadataFile = deployFolder + "/.metadata/metadata.json"
if (fs.existsSync(metadataFile)) { // should always exist but just in case something fucks up...
    const raw = fs.readFileSync(metadataFile, "utf-8");
    const cleaned = raw.replace(/^\uFEFF/, ""); // Remove UTF-8 BOM if present
    const content = JSON.parse(cleaned);
    content.name = "1444 Start Date";
    content.supported_game_version = gameVersion;
    fs.writeFileSync(metadataFile, JSON.stringify(content));
} else {
    throw new Error("Critical Error: .metadata/metadata.json missing or not copied over");
}