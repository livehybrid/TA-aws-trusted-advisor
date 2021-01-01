#sudo apt install npm
echo "" > nextRelease.txt
npx -p @semantic-release/exec -p semantic-release semantic-release --dry-run --plugins "@semantic-release/commit-analyzer,@semantic-release/exec" --analyzeCommits @semantic-release/commit-analyzer --verifyRelease @semantic-release/exec --verifyReleaseCmd 'echo ${nextRelease.version} > nextRelease.txt'
Version=$(cat nextRelease.txt)
echo "============"
echo "$Version"
echo "============"
#source ~/.venv/bin/activate
Version="v0.0.1"
ucc-gen --ta-version="$Version"

                
PACKAGE_ID=$(/bin/ls output/)
BUILD_DIR=output/$PACKAGE_ID
#source ~/.venv/bin/activate
slim generate-manifest $BUILD_DIR --update >/tmp/app.manifest   || true
cp  /tmp/app.manifest  $BUILD_DIR/app.manifest
mkdir -p build/package/splunkbase
mkdir -p build/package/deployment
slim package -o build/package/splunkbase $BUILD_DIR
mkdir -p build/package/deployment
PACKAGE=$(ls build/package/splunkbase/*)
slim partition $PACKAGE -o build/package/deployment/ || true
slim validate $PACKAGE
