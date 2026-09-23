# Shim, not nvm: node is pinned by the image.
nvm() {
  case "$1" in
    use|install)
      want="${2#v}"; want="${want%%.*}"
      have="$(node -v 2>/dev/null)"; have="${have#v}"; have="${have%%.*}"
      if [ -z "$want" ] || [ "$want" = "$have" ] || [ "$want" = "default" ] \
         || [ "$want" = "node" ] || [ "$want" = "lts" ]; then
        echo "Now using node $(node -v) (nvm shim)"
        return 0
      fi
      echo "nvm shim: this image ships node $(node -v); it cannot install v$want" >&2
      return 1 ;;
    current) node -v ;;
    which)   command -v node ;;
    ls|list) node -v ;;
    *)       return 0 ;;
  esac
}
