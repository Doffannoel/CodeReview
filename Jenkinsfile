def notifyGitHubCommit(String status, String description) {
    if (!env.GITHUB_COMMIT_SHA?.trim()) {
        echo 'WARNING: GitHub status was not sent because commit SHA is empty.'
        return
    }

    try {
        githubNotify(
            account: 'IM-corporation',
            repo: 'Marmut-Codereviewer-Graphmethod',
            sha: env.GITHUB_COMMIT_SHA,
            context: 'ci/jenkins',
            status: status,
            description: description,
            targetUrl: env.BUILD_URL,
            credentialsId: 'github_status_credential'
        )
    } catch (Exception notificationError) {
        echo "WARNING: Failed to send GitHub status: ${notificationError.message}"
    }
}
pipeline {
    agent any

    environment {
        GRAPHMETHOD_IMAGE = 'ghcr.io/im-corporation/graphmethod-code-reviewer'
        DEPLOY_DIR = '/opt/platform/graphmethod'
        COMPOSE_FILE = '/opt/platform/graphmethod/compose.yaml'
    }

    triggers {
        githubPush()
    }

    stages {
        stage('Prepare build metadata') {
            steps {
                script {
                    env.GITHUB_COMMIT_SHA = sh(
                        script: 'git rev-parse HEAD',
                        returnStdout: true
                    ).trim()
                    env.IMAGE_TAG = sh(
                        script: 'git rev-parse --short=12 HEAD',
                        returnStdout: true
                    ).trim()
                    notifyGitHubCommit(
                        'PENDING',
                        'Jenkins build is running'
                    )
                }
            }
        }

        stage('Test') {
            steps {
                sh '''
                    set -eu
                    docker build --target test -t graphmethod-test:${IMAGE_TAG} .
                    docker run --rm graphmethod-test:${IMAGE_TAG}
                '''
            }
        }

        stage('Login GHCR') {
            steps {
                withCredentials([
                    usernamePassword(
                        credentialsId: 'ghcr_credential',
                        usernameVariable: 'GHCR_USER',
                        passwordVariable: 'GHCR_TOKEN'
                    )
                ]) {
                    sh 'printf "%s" "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin'
                }
            }
        }

        stage('Build and push') {
            steps {
                sh '''
                    set -eu
                    docker build -t ${GRAPHMETHOD_IMAGE}:${IMAGE_TAG} -t ${GRAPHMETHOD_IMAGE}:latest .
                    docker push ${GRAPHMETHOD_IMAGE}:${IMAGE_TAG}
                    docker push ${GRAPHMETHOD_IMAGE}:latest
                '''
            }
        }

        stage('Prepare deployment') {
            steps {
                withCredentials([
                    string(credentialsId: 'graphmethod_github_app_id', variable: 'GM_GITHUB_APP_ID'),
                    string(credentialsId: 'graphmethod_postgres_password', variable: 'GM_POSTGRES_PASSWORD'),
                    string(credentialsId: 'graphmethod_neo4j_password', variable: 'GM_NEO4J_PASSWORD'),
                    string(credentialsId: 'graphmethod_github_private_key_base64', variable: 'GM_GITHUB_PRIVATE_KEY_BASE64'),
                    string(credentialsId: 'graphmethod_github_webhook_secret', variable: 'GM_GITHUB_WEBHOOK_SECRET'),
                    string(credentialsId: 'graphmethod_gemini_api_key', variable: 'GM_LLM_API_KEY')
                ]) {
                    sh '''
                        set -eu
                        umask 077
                        install -d -m 0700 "${DEPLOY_DIR}" "${DEPLOY_DIR}/secrets"
                        cp deploy/hostinger/compose.yaml "${COMPOSE_FILE}"

                        sed "s/^GITHUB_APP_ID=.*/GITHUB_APP_ID=${GM_GITHUB_APP_ID}/" \
                            deploy/hostinger/runtime.env.example > "${DEPLOY_DIR}/runtime.env"

                        printf '%s' "${GM_POSTGRES_PASSWORD}" > "${DEPLOY_DIR}/secrets/postgres_password"
                        printf '%s' "${GM_NEO4J_PASSWORD}" > "${DEPLOY_DIR}/secrets/neo4j_password"
                        printf '%s' "${GM_GITHUB_PRIVATE_KEY_BASE64}" > "${DEPLOY_DIR}/secrets/github_private_key_base64"
                        printf '%s' "${GM_GITHUB_WEBHOOK_SECRET}" > "${DEPLOY_DIR}/secrets/github_webhook_secret"
                        printf '%s' "${GM_LLM_API_KEY}" > "${DEPLOY_DIR}/secrets/llm_api_key"
                        chmod 0600 "${DEPLOY_DIR}/runtime.env" "${DEPLOY_DIR}/secrets/"*
                    '''
                }
            }
        }

        stage('Deploy') {
            steps {
                sh '''
                    set -eu
                    docker network inspect platform-internal >/dev/null 2>&1 || \
                        docker network create platform-internal
                    docker compose --env-file "${DEPLOY_DIR}/runtime.env" \
                        -f "${COMPOSE_FILE}" config --quiet
                    docker compose --env-file "${DEPLOY_DIR}/runtime.env" \
                        -f "${COMPOSE_FILE}" pull
                    docker compose --env-file "${DEPLOY_DIR}/runtime.env" \
                        -f "${COMPOSE_FILE}" up -d --wait --wait-timeout 240
                '''
            }
        }

        stage('Verify') {
            steps {
                sh '''
                    set -eu
                    docker compose --env-file "${DEPLOY_DIR}/runtime.env" \
                        -f "${COMPOSE_FILE}" ps
                    docker inspect --format='{{.State.Health.Status}}' graphmethod-api | grep -qx healthy
                    docker inspect --format='{{.State.Running}}' graphmethod-worker | grep -qx true
                '''
            }
        }
    }

    post {
        success {
            script {
                notifyGitHubCommit(
                    'SUCCESS',
                    'Jenkins build succeeded'
                )
            }
        }

        failure {
            sh '''
                docker compose --env-file "${DEPLOY_DIR}/runtime.env" \
                    -f "${COMPOSE_FILE}" ps || true
                docker compose --env-file "${DEPLOY_DIR}/runtime.env" \
                    -f "${COMPOSE_FILE}" logs --tail=150 || true
            '''
            script {
                notifyGitHubCommit(
                    'FAILURE',
                    'Jenkins build failed'
                )
            }
        }

        unstable {
            script {
                notifyGitHubCommit(
                    'FAILURE',
                    'Jenkins build is unstable'
                )
            }
        }

        aborted {
            script {
                notifyGitHubCommit(
                    'ERROR',
                    'Jenkins build was aborted'
                )
            }
        }

        always {
            sh 'docker image rm graphmethod-test:${IMAGE_TAG} >/dev/null 2>&1 || true'
            sh 'docker logout ghcr.io || true'
            cleanWs()
        }
    }
}

