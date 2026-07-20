#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${1:-${repo_dir}/build/static-replay-viewer}"
if [[ "${output_dir}" != /* ]]; then
  output_dir="${repo_dir}/${output_dir}"
fi
output_dir="${output_dir%/}"
output_name="$(basename "${output_dir}")"
if [[ -z "${output_dir}" || "${output_name}" == "." || "${output_name}" == ".." ]]; then
  echo "Refusing unsafe replay viewer output directory: ${output_dir:-<empty>}" >&2
  exit 1
fi
mkdir -p "$(dirname "${output_dir}")"
output_dir="$(cd "$(dirname "${output_dir}")" && pwd -P)/${output_name}"
if [[ "${output_dir}" == "/" || "${output_dir}" == "${repo_dir}" ]]; then
  echo "Refusing unsafe replay viewer output directory: ${output_dir}" >&2
  exit 1
fi
nimby_root="${NIMBY_ROOT:-${HOME}/.nimby}"
bitworld_dir="${nimby_root}/pkgs/bitworld"
emcc_bin="${EMCC:-$(command -v emcc || true)}"
nim_bin="${NIM:-$(command -v nim)}"

if [[ ! -x "${emcc_bin}" ]]; then
  echo "emcc is required (install Emscripten or set EMCC)." >&2
  exit 1
fi
if [[ ! -f "${bitworld_dir}/client/global_client.html" ]]; then
  echo "Pinned Bitworld package not found at ${bitworld_dir}; run nimby sync." >&2
  exit 1
fi

expected_bitworld="$(awk '$1 == "bitworld" { print $4; exit }' "${repo_dir}/nimby.lock")"
actual_bitworld="$(git -C "${bitworld_dir}" rev-parse HEAD)"
if [[ -z "${expected_bitworld}" || "${actual_bitworld}" != "${expected_bitworld}" ]]; then
  echo "Bitworld checkout ${actual_bitworld} does not match lock ${expected_bitworld}." >&2
  exit 1
fi

rm -rf "${output_dir}"
mkdir -p "${output_dir}/nimcache"
export EM_CACHE="${output_dir}/nimcache/emcache"

nim_paths=("--path:${repo_dir}/src")
for package_dir in "${nimby_root}"/pkgs/*; do
  [[ -d "${package_dir}/src" ]] && nim_paths+=("--path:${package_dir}/src")
  nim_paths+=("--path:${package_dir}")
done

"${nim_bin}" c \
  --hints:off \
  --threads:off \
  --mm:arc \
  --exceptions:goto \
  --define:emscripten \
  --define:noSignalHandler \
  --define:release \
  --os:linux \
  --cpu:wasm32 \
  --cc:clang \
  --clang.exe:"${emcc_bin}" \
  --clang.linkerexe:"${emcc_bin}" \
  --nimcache:"${output_dir}/nimcache" \
  --out:"${output_dir}/crewrift_core.js" \
  --passL:"-s MODULARIZE=1 -s EXPORT_NAME=createCrewriftCore -s ALLOW_MEMORY_GROWTH=1 -s ENVIRONMENT=web -s EXPORTED_FUNCTIONS=['_cr_load_replay','_cr_advance','_cr_input','_cr_frame_ptr','_cr_frame_len','_cr_tick','_cr_max_tick','_cr_playing','_cr_error_ptr','_malloc','_free'] -s EXPORTED_RUNTIME_METHODS=['UTF8ToString','HEAPU8'] --preload-file ${repo_dir}/data@data --preload-file ${repo_dir}/data@client/data" \
  "${nim_paths[@]}" \
  "${repo_dir}/replay_viewer/crewrift_replay_wasm.nim"

cp "${bitworld_dir}/client/snappyjs.min.js" "${output_dir}/snappyjs.min.js"
cp "${repo_dir}/replay_viewer/static_replay_adapter.js" "${output_dir}/static_replay_adapter.js"

sed '/<script src="snappyjs.min.js"><\/script>/i\
<script src="crewrift_core.js"><\/script>\
<script src="static_replay_adapter.js"><\/script>' \
  "${bitworld_dir}/client/global_client.html" > "${output_dir}/index.html"

rm -rf "${output_dir}/nimcache"
echo "Static Crewrift replay viewer: ${output_dir}"
