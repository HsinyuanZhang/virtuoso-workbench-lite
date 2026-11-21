import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { expect, it } from "vitest";
import { palette } from "./palette";

const allowed = new Set(["000000", "ffffff", "ff0000", "0000ff", "00ff00"]);
const named = new Set("aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato transparent turquoise violet wheat white whitesmoke yellow yellowgreen".split(" "));
const allowedNames = new Set(["black", "white", "red", "blue", "lime", "transparent", "currentcolor", "gray", "grey", "silver", "dimgray", "dimgrey", "darkgray", "darkgrey", "lightgray", "lightgrey", "gainsboro", "whitesmoke"]);

function violations(value: string, css = false): string[] {
  const errors: string[] = [];
  let rest = value.toLowerCase().replace(/var\([^)]*\)/g, "");
  rest = rest.replace(/#[\da-f]{3,8}\b/g, (literal) => {
    let hex = literal.slice(1);
    if (hex.length === 3 || hex.length === 4) hex = [...hex].map((digit) => digit + digit).join("");
    const rgb = hex.slice(0, 6);
    if (![6, 8].includes(hex.length) || (!allowed.has(rgb) && !/^([\da-f]{2})\1\1$/.test(rgb))) errors.push(literal);
    return "";
  });
  rest = rest.replace(/rgba?\(([^)]*)\)/g, (literal: string, components: string) => {
    const parts = components.split(/[\s,/]+/).filter(Boolean);
    const rgb = parts.slice(0, 3).map((part) => part.endsWith("%") ? Number(part.slice(0, -1)) * 2.55 : Number(part));
    const hex = rgb.map((part) => Math.round(part).toString(16).padStart(2, "0")).join("");
    const alpha = parts[3] === undefined ? 1 : parts[3].endsWith("%") ? Number(parts[3].slice(0, -1)) / 100 : Number(parts[3]);
    const neutral = rgb.every((value) => Math.abs(value - rgb[0]) < 1e-8);
    const primary = rgb.every((value) => value === 0 || Math.abs(value - 255) < 1e-8) && allowed.has(hex);
    if (![3, 4].includes(parts.length) || rgb.some((value) => !Number.isFinite(value) || value < 0 || value > 255 + 1e-8) || (!neutral && !primary) || !Number.isFinite(alpha) || alpha < 0 || alpha > 1) errors.push(literal);
    return "";
  });
  if (/\b(?:hsla?|hwb|lab|lch|oklab|oklch|color)\(/.test(rest)) errors.push(rest);
  const words = css || /gradient\(/.test(rest) ? rest.match(/[a-z]+/g) ?? [] : [rest.trim()];
  for (const word of words) if (named.has(word) && !allowedNames.has(word)) errors.push(word);
  return errors;
}

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => entry.isDirectory() ? files(join(directory, entry.name)) : [join(directory, entry.name)]);
}

it("uses only Virtuoso hues, neutral grays, and opacity variants in source CSS and TypeScript", () => {
  const errors: string[] = [];
  const { panel, input, border, ...hues } = palette;
  expect(Object.values(hues).sort()).toEqual([...allowed].map((hex) => String.fromCharCode(35) + hex).sort());
  expect([panel, input, border].every((value) => violations(value).length === 0)).toBe(true);
  for (const path of files(fileURLToPath(new URL(".", import.meta.url))).filter((path) => /\.(css|ts)$/.test(path))) {
    const source = readFileSync(path, "utf8");
    if (path.endsWith(".css")) {
      for (const match of source.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/:\s*([^;{}]+)[;}]/g)) errors.push(...violations(match[1], true).map((color) => `${path}: ${color}`));
    } else {
      const visit = (node: ts.Node): void => {
        if (ts.isStringLiteralLike(node)) errors.push(...violations(node.text).map((color) => `${path}: ${color}`));
        ts.forEachChild(node, visit);
      };
      visit(ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true));
    }
  }
  expect(errors).toEqual([]);
});

it("rejects off-palette hex, RGB, named colors and gradients", () => {
  const hash = String.fromCharCode(35);
  for (const value of [hash + "f80", hash + "00ffff", hash + "242425", "rgb(" + "128 128 129)", "rgb(" + "256 256 256)", "rgb(" + "128 128 128 / 2)", "or" + "ange", "linear-gradient(" + "white, purple)"]) expect(violations(value)).not.toEqual([]);
  for (const value of [...Object.values(palette), hash + "fff8", hash + "888", hash + "80808080", "rgb(" + "128 128 128)", "rgba(" + "128,128,128,0.5)", "rgb(" + "50% 50% 50% / 25%)", "rgb(" + "255 255 255 / .25)", "gray"]) expect(violations(value)).toEqual([]);
});
