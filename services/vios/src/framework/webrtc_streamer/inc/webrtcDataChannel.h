/*
 * SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once

#include <logger.h>
#include "api/peer_connection_interface.h"
#include "datachannellistenerinterface.h"
#include <thread>

using namespace std;

#define GET_DATA_CHANNEL WebrtcDataChannel::getInstance

class WebrtcDataChannelOberver : public webrtc::DataChannelObserver
{
public:
	WebrtcDataChannelOberver(rtc::scoped_refptr<webrtc::DataChannelInterface> dataChannel,
							 std::set<IDataChannelListener *> listeners) : m_dataChannel(dataChannel), m_listeners(listeners)
	{
		if (m_dataChannel)
		{
			m_dataChannel->RegisterObserver(this);
			LOG(info) << "Created webrtc data channel observer" << endl;
		}
	}
	virtual ~WebrtcDataChannelOberver()
	{
		LOG(info) << __PRETTY_FUNCTION__ << endl;
		if (m_dataChannel)
		{
			m_dataChannel->UnregisterObserver();
			LOG(info) << "Exiting from webrtc data channel observer" << endl;
		}
	}

	// DataChannelObserver interface
	virtual void OnStateChange()
	{
		if (m_dataChannel)
		{
			LOG(verbose) << "data channel: " << m_dataChannel->label() << " state: " << webrtc::DataChannelInterface::DataStateString(m_dataChannel->state()) << endl;
		}
	}
	virtual void OnMessage(const webrtc::DataBuffer &buffer)
	{
		if (m_dataChannel)
		{
			std::thread asyncTask([=]()
			{
				std::string msg((const char *)buffer.data.data(), buffer.data.size());
				{
					std::lock_guard<std::mutex> listenerLock(m_listenerMutex);
					// invoke the listeners for this peer observer
					for (auto listener : m_listeners)
					{
						Json::Value jMessage = stringToJson(msg);
						listener->onMessage(jMessage);
					}
				}
			});
			asyncTask.detach();
		}
	}

	bool sendMessage(string msg)
	{
		if (m_dataChannel)
		{
			webrtc::DataBuffer buffer(msg);
			if (m_dataChannel->Send(buffer) == false)
			{
				LOG(error) << "Failed to send message on data channel" << endl;
				return false;
			}
			return true;
		}
		return false;
	}
	// register listener for this peer observer
	void registerListener(IDataChannelListener *listener)
	{
		std::lock_guard<std::mutex> listenerLock(m_listenerMutex);
		m_listeners.insert(listener);
	}
	// de-register listener for this peer observer
	void deRegisterListener(IDataChannelListener *listener)
	{
		std::lock_guard<std::mutex> listenerLock(m_listenerMutex);
		m_listeners.erase(listener);
	}

protected:
	rtc::scoped_refptr<webrtc::DataChannelInterface> m_dataChannel;

	std::mutex m_listenerMutex;
	std::set<IDataChannelListener *> m_listeners;
};

class WebrtcDataChannel
{
public:
	static WebrtcDataChannel *getInstance()
	{
		if (m_instance == nullptr)
		{
			m_instance = new WebrtcDataChannel();
		}
		return m_instance;
	}
	static void deleteDataChannelInstance()
	{
		if (m_instance != nullptr)
		{
			delete m_instance;
			m_instance = nullptr;
		}
	}

	void addChannelObserver(std::string clientId, rtc::scoped_refptr<webrtc::DataChannelInterface> channel);
	void removeChannelObserver(std::string clientId);
	bool sendMessageOnDataChannel(std::string clientId, std::string msg);
	void sendMessageOnAllDataChannels(std::string msg);
	bool isConnected(std::string clientId);

	void registerListener(IDataChannelListener *listener);
	void deRegisterListener(IDataChannelListener *listener);

private:
	WebrtcDataChannel()
	{
		LOG(verbose) << __PRETTY_FUNCTION__ << endl;
	}
	~WebrtcDataChannel()
	{
		LOG(info) << __PRETTY_FUNCTION__ << endl;
	}
	static WebrtcDataChannel *m_instance;

	std::map<std::string, std::shared_ptr<WebrtcDataChannelOberver>> m_channelObserverMap;
	std::mutex m_channelMapMutex;

	std::mutex m_listenerMutex;
	std::set<IDataChannelListener *> m_listeners;
};
