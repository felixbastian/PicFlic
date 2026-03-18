gcloud iam service-accounts create github-deployer \
  --display-name="GitHub Deployer"

gcloud iam service-accounts create cloud-run-runtime \
  --display-name="Cloud Run Runtime"

gcloud projects add-iam-policy-binding picflic-490614 \
  --member="serviceAccount:github-deployer@picflic-490614.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding picflic-490614 \
  --member="serviceAccount:github-deployer@picflic-490614.iam.gserviceaccount.com" \
  --role="roles/run.admin"

gcloud iam service-accounts add-iam-policy-binding \
  cloud-run-runtime@picflic-490614.iam.gserviceaccount.com \
  --project=picflic-490614 \
  --member="serviceAccount:github-deployer@picflic-490614.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

gcloud iam workload-identity-pools create github-pool \
  --project=picflic-490614 \
  --location=global \
  --display-name="GitHub pool"




gcloud iam workload-identity-pools providers create-oidc github-provider \
  --project=picflic-490614 \
  --location=global \
  --workload-identity-pool=github-pool \
  --display-name="GitHub provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='felixbastian/PicFlic'"

gcloud iam service-accounts add-iam-policy-binding \
  github-deployer@picflic-490614.iam.gserviceaccount.com \
  --project=picflic-490614 \
  --member="principalSet://iam.googleapis.com/projects/642164961505/locations/global/workloadIdentityPools/github-pool/attribute.repository/felixbastian/PicFlic" \
  --role="roles/iam.workloadIdentityUser"






gcloud run services add-iam-policy-binding SERVICE \
  --region=europe-west1 \
  --member="allUsers" \
  --role="roles/run.invoker"