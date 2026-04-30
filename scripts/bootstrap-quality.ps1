param(
	[string]$ProjectPath = "."
)

$ErrorActionPreference = "Stop"

function Write-Step {
	param([string]$Message)
	Write-Host "[quality-bootstrap] $Message"
}

function Invoke-External {
	param(
		[string]$FilePath,
		[string[]]$ArgumentList,
		[switch]$AllowFailure
	)

	& $FilePath @ArgumentList
	if ($LASTEXITCODE -ne 0) {
		$joinedArgs = ($ArgumentList -join " ")
		$message = "Command failed: $FilePath $joinedArgs (exit code $LASTEXITCODE)"
		if ($AllowFailure) {
			Write-Step "$message (continuing)"
			return
		}
		throw $message
	}
}

$resolvedPath = Resolve-Path $ProjectPath
Set-Location $resolvedPath

if (-not (Test-Path "package.json")) {
	throw "No package.json found at: $resolvedPath"
}

Write-Step "Installing dev dependencies (husky, lint-staged)..."
Invoke-External -FilePath "npm" -ArgumentList @("i", "-D", "husky", "lint-staged")

Write-Step "Updating package.json scripts and lint-staged config..."
@'
const fs = require("fs");
const pkgPath = "package.json";
const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));

pkg.scripts = pkg.scripts || {};

if (!pkg.scripts.prepare) {
  pkg.scripts.prepare = "husky";
} else if (!/\bhusky\b/.test(pkg.scripts.prepare)) {
  pkg.scripts.prepare = `${pkg.scripts.prepare} && husky`;
}

if (!pkg.scripts["lint:fix"]) {
  pkg.scripts["lint:fix"] = pkg.scripts.lint ? `${pkg.scripts.lint} --fix` : "eslint . --fix";
}

pkg["lint-staged"] = pkg["lint-staged"] || {};
const lintStagedKeys = Object.keys(pkg["lint-staged"]);
const hasJsTsRule = lintStagedKeys.some((key) =>
  key.includes("*.ts") ||
  key.includes("*.tsx") ||
  key.includes("*.js") ||
  key.includes("*.jsx") ||
  key.includes("{js,jsx,ts,tsx}") ||
  key.includes("{ts,tsx}")
);

if (!hasJsTsRule) {
  pkg["lint-staged"]["**/*.{js,jsx,ts,tsx}"] = "eslint --fix --max-warnings=0";
}

fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n");
'@ | node -

Write-Step "Ensuring .husky/pre-commit hook exists..."
if (-not (Test-Path ".husky")) {
	New-Item -ItemType Directory -Path ".husky" | Out-Null
}

@'
#!/usr/bin/env sh
npx lint-staged
'@ | Set-Content -Path ".husky/pre-commit" -Encoding ascii

if (Test-Path ".git") {
	Write-Step "Pointing Git hooksPath to .husky..."
	Invoke-External -FilePath "git" -ArgumentList @("config", "core.hooksPath", ".husky") -AllowFailure
}

Write-Step "Done. Commits will now run lint-staged before completing."
