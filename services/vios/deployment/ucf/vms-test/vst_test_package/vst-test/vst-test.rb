require "selenium-webdriver"
require "rspec"

describe "VST test" do
  describe "webrtc" do
    it "webrtc live playback" do
			driver = Selenium::WebDriver.for :firefox
			driver.manage.window.resize_to(1080, 1920)
			# Go to signup form
			driver.navigate.to "http://vms-vms-svc:30000/tokkio_test.html"
			# Fill out and submit form
			button = driver.find_element(id: 'startTestId')
			button.click
			wait = Selenium::WebDriver::Wait.new(:timeout => 15)
			dropDownMenu = wait.until{ driver.find_element(id: 'cameraDropdown') }
			button = driver.find_element(id: 'submitFormButton')
			button.click
			puts "starting..."
			passElement = driver.find_element(:class, 'passes')
			failElement = driver.find_element(:class, 'failures')
			passedCases = passElement.find_element(:tag_name, 'em').attribute('innerHTML')
			failedCases = failElement.find_element(:tag_name, 'em').attribute('innerHTML')
			totalCases = passedCases.to_i + failedCases.to_i
			time = 0
			while totalCases != 43 && time <= 600
				passedCases = passElement.find_element(:tag_name, 'em').attribute('innerHTML')
				failedCases = failElement.find_element(:tag_name, 'em').attribute('innerHTML')
				totalCases = passedCases.to_i + failedCases.to_i
				time = time + 5
				sleep 5
                                puts "total %s cases passed out of 43" % [passedCases]
			end
			puts "taking ss"
			driver.save_screenshot 'ss.png'
			if passedCases.to_i != 43
				raise "test cases failed %s" % [failedCases]
			end
			puts "done"
			driver.quit
		end
  end
end
