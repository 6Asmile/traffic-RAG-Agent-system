//
// Generated file. Do not edit.
// This file is generated from template in file `flutter_tools/lib/src/flutter_plugins.dart`.
//

// @dart = 3.10

import 'dart:io'; // flutter_ignore: dart_io_import.
import 'package:image_picker_android/image_picker_android.dart' as image_picker_android;
import 'package:path_provider_android/path_provider_android.dart' as path_provider_android;
import 'package:webview_flutter_android/webview_flutter_android.dart' as webview_flutter_android;
import 'package:image_picker_ios/image_picker_ios.dart' as image_picker_ios;
import 'package:path_provider_foundation/path_provider_foundation.dart' as path_provider_foundation;
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart' as webview_flutter_wkwebview;
import 'package:file_selector_linux/file_selector_linux.dart' as file_selector_linux;
import 'package:image_picker_linux/image_picker_linux.dart' as image_picker_linux;
import 'package:package_info_plus/package_info_plus.dart' as package_info_plus;
import 'package:path_provider_linux/path_provider_linux.dart' as path_provider_linux;
import 'package:file_selector_macos/file_selector_macos.dart' as file_selector_macos;
import 'package:image_picker_macos/image_picker_macos.dart' as image_picker_macos;
import 'package:path_provider_foundation/path_provider_foundation.dart' as path_provider_foundation;
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart' as webview_flutter_wkwebview;
import 'package:file_selector_windows/file_selector_windows.dart' as file_selector_windows;
import 'package:flutter_secure_storage_windows/flutter_secure_storage_windows.dart' as flutter_secure_storage_windows;
import 'package:image_picker_windows/image_picker_windows.dart' as image_picker_windows;
import 'package:package_info_plus/package_info_plus.dart' as package_info_plus;
import 'package:path_provider_windows/path_provider_windows.dart' as path_provider_windows;

@pragma('vm:entry-point')
class _PluginRegistrant {

  @pragma('vm:entry-point')
  static void register() {
    if (Platform.isAndroid) {
      try {
        image_picker_android.ImagePickerAndroid.registerWith();
      } catch (err) {
        print(
          '`image_picker_android` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        path_provider_android.PathProviderAndroid.registerWith();
      } catch (err) {
        print(
          '`path_provider_android` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        webview_flutter_android.AndroidWebViewPlatform.registerWith();
      } catch (err) {
        print(
          '`webview_flutter_android` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    } else if (Platform.isIOS) {
      try {
        image_picker_ios.ImagePickerIOS.registerWith();
      } catch (err) {
        print(
          '`image_picker_ios` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        path_provider_foundation.PathProviderFoundation.registerWith();
      } catch (err) {
        print(
          '`path_provider_foundation` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        webview_flutter_wkwebview.WebKitWebViewPlatform.registerWith();
      } catch (err) {
        print(
          '`webview_flutter_wkwebview` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    } else if (Platform.isLinux) {
      try {
        file_selector_linux.FileSelectorLinux.registerWith();
      } catch (err) {
        print(
          '`file_selector_linux` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        image_picker_linux.ImagePickerLinux.registerWith();
      } catch (err) {
        print(
          '`image_picker_linux` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        package_info_plus.PackageInfoPlusLinuxPlugin.registerWith();
      } catch (err) {
        print(
          '`package_info_plus` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        path_provider_linux.PathProviderLinux.registerWith();
      } catch (err) {
        print(
          '`path_provider_linux` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    } else if (Platform.isMacOS) {
      try {
        file_selector_macos.FileSelectorMacOS.registerWith();
      } catch (err) {
        print(
          '`file_selector_macos` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        image_picker_macos.ImagePickerMacOS.registerWith();
      } catch (err) {
        print(
          '`image_picker_macos` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        path_provider_foundation.PathProviderFoundation.registerWith();
      } catch (err) {
        print(
          '`path_provider_foundation` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        webview_flutter_wkwebview.WebKitWebViewPlatform.registerWith();
      } catch (err) {
        print(
          '`webview_flutter_wkwebview` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    } else if (Platform.isWindows) {
      try {
        file_selector_windows.FileSelectorWindows.registerWith();
      } catch (err) {
        print(
          '`file_selector_windows` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        flutter_secure_storage_windows.FlutterSecureStorageWindows.registerWith();
      } catch (err) {
        print(
          '`flutter_secure_storage_windows` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        image_picker_windows.ImagePickerWindows.registerWith();
      } catch (err) {
        print(
          '`image_picker_windows` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        package_info_plus.PackageInfoPlusWindowsPlugin.registerWith();
      } catch (err) {
        print(
          '`package_info_plus` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

      try {
        path_provider_windows.PathProviderWindows.registerWith();
      } catch (err) {
        print(
          '`path_provider_windows` threw an error: $err. '
          'The app may not function as expected until you remove this plugin from pubspec.yaml'
        );
      }

    }
  }
}
