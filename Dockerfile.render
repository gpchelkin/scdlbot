# "v4-" stacks use our new, more rigorous buildpacks management system. They
# allow you to use multiple buildpacks in a single application, as well as to
# use custom buildpacks.
#
# - `v2-` images work with heroku-import v3.x.
# - `v4-` images work with heroku-import v4.x. (We synced the tags.)

ARG IMPORT_VERSION=v4
ARG HEROKU_STACK=${IMPORT_VERSION}-heroku-22
FROM ghcr.io/renderinc/heroku-app-builder:${HEROKU_STACK} AS builder


# Below, please specify any build-time environment variables that you need to
# reference in your build (as called by your buildpacks). If you don't specify
# the arg below, you won't be able to access it in your build. You can also
# specify a default value, as with any Docker `ARG`, if appropriate for your
# use case.

# ARG MY_BUILD_TIME_ENV_VAR
# ARG DATABASE_URL

# The FROM statement above refers to an image with the base buildpacks already
# in place. We then run the apply-buildpacks.py script here because, unlike our
# `v2` image, this allows us to expose build-time env vars to your app.
RUN /render/build-scripts/apply-buildpacks.py ${HEROKU_STACK}

# We strongly recommend that you package a Procfile with your application, but
# if you don't, we'll try to guess one for you. If this is incorrect, please
# add a Procfile that tells us what you need us to run.
RUN if [[ -f /app/Procfile ]]; then \
  /render/build-scripts/create-process-types "/app/Procfile"; \
fi;

# For running the app, we use a clean base image and also one without Ubuntu development packages
# https://devcenter.heroku.com/articles/heroku-20-stack#heroku-20-docker-image
FROM ghcr.io/renderinc/heroku-app-runner:${HEROKU_STACK} AS runner

# Here we copy your build artifacts from the build image to the runner so that
# the image that we deploy to Render is smaller and, therefore, can start up
# faster.
COPY --from=builder --chown=1000:1000 /render /render/
COPY --from=builder --chown=1000:1000 /app /app/

# Here we're switching to a non-root user in the container to remove some categories
# of container-escape attack.
USER 1000:1000
WORKDIR /app

# This sources all /app/.profile.d/*.sh files before process start.
# These are created by buildpacks, and you probably don't have to worry about this.
# https://devcenter.heroku.com/articles/buildpack-api#profile-d-scripts
ENTRYPOINT [ "/render/setup-env" ]

# 3. By default, we run the 'web' process type defined in the app's Procfile
# You may override the process type that is run by replacing 'web' with another
# process type name in the CMD line below. That process type must have been
# defined in the app's Procfile during build.
CMD [ "/render/process/web" ]
