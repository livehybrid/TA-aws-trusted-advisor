PACKAGE_ID=$(ls output/)
BUILD_DIR=output/$PACKAGE_ID
source ~/.venv/bin/activate
slim generate-manifest $BUILD_DIR --update >/tmp/app.manifest   || true
cp  /tmp/app.manifest  $BUILD_DIR/app.manifest
mkdir -p build/package/splunkbase
mkdir -p build/package/deployment
slim package -o build/package/splunkbase $BUILD_DIR
mkdir -p build/package/deployment
PACKAGE=$(ls build/package/splunkbase/*)
slim partition $PACKAGE -o build/package/deployment/ || true
slim validate $PACKAGE