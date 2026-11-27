import { createHmac, randomBytes, randomUUID } from "node:crypto";
import {
  closeSync, constants, fstatSync, fsyncSync, linkSync, lstatSync, mkdirSync,
  openSync, readFileSync, renameSync, rmSync, writeFileSync,
} from "node:fs";
import path from "node:path";

export const sjabloon = "mediaguard-prerender.json";
export const runtimeMap = "/run/mediaguard-web/mediaguard-next";
export const prerender = "prerender-manifest.json";
export const previewProps = "server/preview-props.json";
export const actieJson = "server/server-reference-manifest.json";
export const actieJs = "server/server-reference-manifest.js";
export const jsPrefix = "self.__RSC_SERVER_MANIFEST=";

function privemap(map) {
  mkdirSync(map, { recursive: true, mode: 0o700 });
  const info = lstatSync(map);
  if (!info.isDirectory() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o700) {
    throw new Error("De websleutelmap vereist de webgebruiker als eigenaar en modus 0700");
  }
}

function leesSleutel(bestand) {
  const fd = openSync(bestand, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o600 || info.size !== 32) {
      throw new Error("De bewaarde websleutel vereist 32 bytes en modus 0600");
    }
    return readFileSync(fd);
  } finally {
    closeSync(fd);
  }
}

// Losse webcontainer. Publiceer een volledig geschreven sleutel één keer, ook bij twee starters.
export function installatiesleutel(map) {
  privemap(map);
  const bestand = path.join(map, "server-actions.key");
  try {
    return leesSleutel(bestand);
  } catch (fout) {
    if (fout.code !== "ENOENT") throw fout;
  }
  const tijdelijk = path.join(map, randomUUID());
  writeFileSync(tijdelijk, randomBytes(32), { flag: "wx", mode: 0o600, flush: true });
  try {
    try {
      linkSync(tijdelijk, bestand);
    } catch (fout) {
      if (fout.code !== "EEXIST") throw fout;
    }
    const mapFd = openSync(map, constants.O_DIRECTORY);
    try {
      fsyncSync(mapFd);
    } finally {
      closeSync(mapFd);
    }
    return leesSleutel(bestand);
  } finally {
    rmSync(tijdelijk);
  }
}

export function schrijfManifesten(nextMap, runtime, sleutel) {
  if (sleutel.length !== 32) throw new Error("Ongeldige installatiesleutel voor de webserver");
  privemap(runtime);
  const afgeleid = (doel) => createHmac("sha256", sleutel).update(doel).digest("hex");
  const preview = {
    previewModeId: afgeleid("preview-id").slice(0, 32),
    previewModeSigningKey: afgeleid("preview-ondertekening"),
    previewModeEncryptionKey: afgeleid("preview-versleuteling"),
  };
  const routes = JSON.parse(readFileSync(path.join(nextMap, sjabloon), "utf8"));
  const encryptionKey = sleutel.toString("base64");
  const acties = JSON.stringify({ node: {}, edge: {}, encryptionKey });
  for (const [naam, inhoud] of [
    [prerender, JSON.stringify({ ...routes, preview })],
    [previewProps, JSON.stringify(preview)],
    [actieJson, acties],
    [actieJs, jsPrefix + JSON.stringify(acties)],
  ]) {
    const tijdelijk = path.join(runtime, randomUUID());
    writeFileSync(tijdelijk, inhoud, { mode: 0o600, flag: "wx" });
    try {
      renameSync(tijdelijk, path.join(runtime, path.basename(naam)));
    } finally {
      rmSync(tijdelijk, { force: true });
    }
  }
  return encryptionKey;
}
