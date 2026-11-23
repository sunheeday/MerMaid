plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.mermaid.passenger"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.mermaid.passenger"
        minSdk = 31          // Android 12 (Galaxy S22+ 출시 OS). CDM 기기 존재 감지가 API 31부터
        targetSdk = 35       // Android 15 (One UI 7)
        versionCode = 2
        versionName = "0.2.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    buildFeatures {
        compose = true
        buildConfig = true   // BuildConfig.DEBUG → 데모 모드 버튼
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")

    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    // JVM 단위 시험 (파싱·링버퍼·Hz 계산). org.json은 android.jar에 껍데기만 있어 시험에서만 진짜를 넣는다
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
